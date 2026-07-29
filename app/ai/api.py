"""FastAPI приложение Pass Docs Local AI.

Аутентификация: X-Api-Key + роль (viewer/operator/reviewer/admin).
operator_id берётся из description токена — клиент не может его подделать.
Production-БД не изменяется ни при каких условиях.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status

from app.ai.adapters import DASHttpAdapter, get_das_adapter, load_package_snapshot
from app.ai.auth import AuthContext, TokenRegistry, require_role
from app.ai.config import AISettings
from app.ai.db import AIRepository
from app.ai.providers import VisionProvider, create_provider
from app.ai.schemas import (
    DocumentSnapshot,
    EmployeeSnapshot,
    ReviewUpdate,
    VehicleAnalyzeRequest,
    VehicleDocumentSnapshot,
    VehicleSnapshot,
)
from app.ai.service import DocumentAIService, VehicleAIService
from app.services.input_loader import InputLoaderError


def create_app(
    *,
    settings: AISettings | None = None,
    repository: AIRepository | None = None,
    provider: VisionProvider | None = None,
    token_registry: TokenRegistry | None = None,
) -> FastAPI:
    resolved_settings = settings or AISettings.from_env()
    resolved_repository = repository or AIRepository(resolved_settings.database_path)
    resolved_repository.initialize()
    resolved_provider = provider or create_provider(resolved_settings)
    # Backward compat: если реестр не передан, но задан api_key в settings,
    # создаём реестр с одним admin-токеном из settings.api_key.
    if token_registry is not None:
        resolved_registry = token_registry
    else:
        env_registry = TokenRegistry.from_env()
        if env_registry.is_empty() and resolved_settings.api_key:
            from app.ai.auth import TokenEntry, _sha256

            env_registry._entries.append(
                TokenEntry(
                    token_hash=_sha256(resolved_settings.api_key),
                    role="admin",
                    description="legacy-api-key",
                )
            )
        resolved_registry = env_registry

    resolved_das_adapter: DASHttpAdapter | None = get_das_adapter()

    service = DocumentAIService(
        settings=resolved_settings,
        repository=resolved_repository,
        provider=resolved_provider,
    )
    vehicle_service = VehicleAIService(
        settings=resolved_settings,
        repository=resolved_repository,
        provider=resolved_provider,
    )

    app = FastAPI(
        title="Pass Docs Local AI",
        version="0.3.0",
        description=(
            "Локальный sidecar для анализа документов. Production-БД не изменяется. Shadow mode only."
        ),
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = resolved_settings
    app.state.repository = resolved_repository
    app.state.provider = resolved_provider
    app.state.token_registry = resolved_registry
    app.state.service = service
    app.state.vehicle_service = vehicle_service

    # ------------------------------------------------------------------ helpers

    def _audit(
        auth: AuthContext,
        action: str,
        entity_type: str,
        entity_id: str | None,
        details: dict | None = None,
    ) -> None:
        resolved_repository.audit(
            actor_type="user",
            actor_id=auth.operator_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=auth.ip,
            role=auth.role,
            details=details or {},
        )

    def _load_employee(employee_id: str | None = None) -> EmployeeSnapshot:
        if resolved_das_adapter is not None:
            if employee_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="employee_id обязателен при AI_DATA_ADAPTER=das",
                )
            try:
                return resolved_das_adapter.load_employee(employee_id)
            except PermissionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="DAS: неверный токен аутентификации",
                ) from exc
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Сотрудник {employee_id} не найден в DAS",
                ) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"DAS adapter недоступен: {type(exc).__name__}",
                ) from exc
        try:
            emp = load_package_snapshot(resolved_settings.input_json_path)
            if employee_id is not None and emp.employee_id != employee_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Сотрудник {employee_id} не найден",
                )
            return emp
        except HTTPException:
            raise
        except (FileNotFoundError, InputLoaderError, ValueError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Источник сотрудников недоступен: {type(exc).__name__}",
            ) from exc

    def _resolve_source_path(raw: str) -> Path:
        """Проверяет, что путь находится внутри data/input — произвольный доступ запрещён."""
        candidate = Path(raw).resolve()
        data_root = (resolved_settings.input_json_path.parent).resolve()
        try:
            candidate.relative_to(data_root)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"source_path должен быть внутри {data_root}",
            ) from None
        if not candidate.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл документа не найден")
        return candidate

    # ------------------------------------------------------------------ health (открыт)

    @app.get("/api/ai/health")
    def health() -> dict:
        database = resolved_repository.health()
        model = resolved_provider.health()
        if resolved_das_adapter is not None:
            das_health = resolved_das_adapter.health()
            # Only ok/degraded exposed publicly — internal codes (auth_error/unavailable/error) omitted
            adapter_info: dict = {
                "type": "das",
                "status": "ok" if das_health["status"] == "ok" else "degraded",
            }
        else:
            adapter_info = {
                "type": "json",
                "status": "ok" if resolved_settings.input_json_path.is_file() else "unavailable",
            }
        overall = (
            "ok"
            if database["status"] == "ok"
            and (not resolved_settings.enabled or model["status"] == "ok")
            and adapter_info.get("status") == "ok"
            else "degraded"
        )
        return {
            "status": overall,
            "enabled": resolved_settings.enabled,
            "database": database,
            "model": model,
            "input_adapter": adapter_info,
            "read_only_production": True,
            "shadow_mode": True,
        }

    # ------------------------------------------------------------------ employees

    @app.get("/api/ai/summary")
    def summary(
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        _audit(auth, "api_read", "summary", None)
        return resolved_repository.summary()

    @app.get("/api/ai/review-queue")
    def review_queue(
        queue_status: Annotated[
            str, Query(alias="status", pattern="^(pending|confirmed|rejected)$")
        ] = "pending",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        _audit(auth, "api_read", "review_queue", queue_status)
        items = resolved_repository.list_review_queue(status=queue_status, limit=limit, offset=offset)
        return {
            "status": queue_status,
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    @app.patch("/api/ai/review-queue/{item_id}")
    def update_review_queue(
        item_id: int,
        payload: ReviewUpdate,
        auth: AuthContext = Depends(require_role("reviewer")),
    ) -> dict:
        item = resolved_repository.update_review(
            item_id=item_id,
            status=payload.status,
            operator_id=auth.operator_id,
            comment=payload.comment,
            reason=payload.reason,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Элемент очереди не найден")
        _audit(
            auth,
            f"review_{payload.status}",
            "review_item",
            str(item_id),
            {"comment": payload.comment, "reason": payload.reason},
        )
        return item

    @app.get("/api/ai/employees/{employee_id}/completeness")
    def employee_completeness(
        employee_id: str,
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        employee = _load_employee(employee_id)
        _audit(auth, "api_read", "employee", employee_id)
        return service.check_completeness(employee, persist=False)

    @app.post("/api/ai/employees/{employee_id}/analyze")
    def analyze_employee(
        employee_id: str,
        auth: AuthContext = Depends(require_role("operator")),
    ) -> dict:
        """Запускает AI-анализ всех документов сотрудника.

        При AI_DATA_ADAPTER=das файлы скачиваются из DAS в temp-директорию,
        анализируются и удаляются. Production-БД не изменяется.
        """
        import tempfile

        employee = _load_employee(employee_id)
        _audit(auth, "employee_analyze_start", "employee", employee_id)

        if resolved_das_adapter is not None:
            download_errors: list[dict] = []
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                local_docs: list[DocumentSnapshot] = []
                for doc in employee.documents:
                    try:
                        local_path = resolved_das_adapter.download_document_file(
                            doc.document_id,
                            dest_dir=tmp,
                            max_size_mb=resolved_settings.max_file_size_mb,
                        )
                        local_docs.append(
                            DocumentSnapshot(
                                document_id=doc.document_id,
                                document_code=doc.document_code,
                                source_path=local_path,
                            )
                        )
                    except Exception as exc:
                        download_errors.append(
                            {"document_id": doc.document_id, "error": f"{type(exc).__name__}: {exc}"}
                        )
                local_employee = EmployeeSnapshot(
                    employee_id=employee.employee_id,
                    full_name=employee.full_name,
                    iin=employee.iin,
                    documents=tuple(local_docs),
                )
                try:
                    result = service.analyze_employee(local_employee)
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=f"Ошибка анализа: {type(exc).__name__}",
                    ) from exc
            result["download_errors"] = download_errors
        else:
            try:
                result = service.analyze_employee(employee)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Ошибка анализа: {type(exc).__name__}",
                ) from exc

        _audit(auth, "employee_analyze_done", "employee", employee_id)
        return result

    @app.get("/api/ai/documents/{document_id}/analysis")
    def document_analysis(
        document_id: str,
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        _audit(auth, "api_read", "document", document_id)
        result = resolved_repository.document_analysis(document_id)
        if not result["runs"] and not result["extractions"] and not result["findings"]:
            raise HTTPException(status_code=404, detail="Анализ документа не найден")
        return result

    # ------------------------------------------------------------------ vehicles

    @app.get("/api/ai/vehicles/summary")
    def vehicle_summary(
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        _audit(auth, "api_read", "vehicle_summary", None)
        return resolved_repository.vehicle_summary()

    @app.get("/api/ai/vehicles/review-queue")
    def vehicle_review_queue(
        queue_status: Annotated[
            str, Query(alias="status", pattern="^(pending|confirmed|rejected)$")
        ] = "pending",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        _audit(auth, "api_read", "vehicle_review_queue", queue_status)
        items = resolved_repository.list_vehicle_review_queue(status=queue_status, limit=limit, offset=offset)
        return {
            "status": queue_status,
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    @app.patch("/api/ai/vehicles/review-queue/{item_id}")
    def update_vehicle_review_queue(
        item_id: int,
        payload: ReviewUpdate,
        auth: AuthContext = Depends(require_role("reviewer")),
    ) -> dict:
        item = resolved_repository.update_vehicle_review(
            item_id=item_id,
            status=payload.status,
            operator_id=auth.operator_id,
            comment=payload.comment,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Элемент очереди не найден")
        _audit(
            auth,
            f"vehicle_review_{payload.status}",
            "vehicle_review_item",
            str(item_id),
            {"comment": payload.comment},
        )
        return item

    @app.post("/api/ai/vehicles/{vehicle_id}/analyze")
    def analyze_vehicle(
        vehicle_id: str,
        payload: VehicleAnalyzeRequest,
        auth: AuthContext = Depends(require_role("operator")),
    ) -> dict:
        """Запускает AI-анализ одного документа ТС.

        source_path — путь к файлу на сервере, должен быть внутри data/input/.
        Все AI-значения попадают в review queue и требуют подтверждения оператором.
        Production-БД не изменяется.
        """
        source_path = _resolve_source_path(payload.source_path)
        vehicle = VehicleSnapshot(
            vehicle_id=vehicle_id,
            plate_number=payload.plate_number,
            make=payload.make,
            model=payload.vehicle_model,
            vehicle_type=payload.vehicle_type,
            color=payload.color,
            owner=payload.owner,
            organization=payload.organization,
            driver=payload.driver,
            site_object=payload.site_object,
            pass_number=payload.pass_number,
            pass_start=None,
            pass_end=None,
            status="",
            access_zone="",
        )
        document = VehicleDocumentSnapshot(
            document_id=payload.document_id,
            document_code=payload.document_code,
            source_path=source_path,
        )
        _audit(
            auth,
            "vehicle_analyze_start",
            "vehicle",
            vehicle_id,
            {"document_code": payload.document_code, "document_id": payload.document_id},
        )
        try:
            result = vehicle_service.analyze_vehicle_document(vehicle, document)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Ошибка анализа: {type(exc).__name__}",
            ) from exc
        _audit(auth, "vehicle_analyze_done", "vehicle", vehicle_id)
        return result

    @app.get("/api/ai/vehicles/{vehicle_id}/completeness")
    def vehicle_completeness(
        vehicle_id: str,
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        _audit(auth, "api_read", "vehicle", vehicle_id)
        from app.ai.vehicles import REQUIRED_VEHICLE_CODES

        return resolved_repository.vehicle_completeness(
            vehicle_id, required_codes=sorted(REQUIRED_VEHICLE_CODES)
        )

    @app.get("/api/ai/vehicles/{vehicle_id}/analysis")
    def vehicle_analysis(
        vehicle_id: str,
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        _audit(auth, "api_read", "vehicle", vehicle_id)
        return resolved_repository.vehicle_analysis(vehicle_id)

    # ------------------------------------------------------------------ metrics

    @app.get("/api/ai/metrics")
    def evaluation_metrics(
        annotations_dir: Annotated[str, Query(max_length=512)] = "",
        auth: AuthContext = Depends(require_role("admin")),
    ) -> dict:
        """Precision / recall / F1 / coverage from manually annotated ground truth.

        Reads *.json files from data/annotations/ (or the path in annotations_dir).
        Returns empty metrics if no annotation files are present.
        Coverage is reported separately and is NOT labelled as accuracy.
        """
        from pathlib import Path

        from app.ai.metrics import load_annotations_dir, calculate_metrics

        ann_path = (
            Path(annotations_dir)
            if annotations_dir
            else resolved_settings.input_json_path.parent.parent / "annotations"
        )
        _audit(auth, "api_read", "metrics", str(ann_path))
        annotations = load_annotations_dir(ann_path)
        report = calculate_metrics(annotations)
        return report.as_dict()

    # ------------------------------------------------------------------ admin

    @app.get("/api/ai/audit-log")
    def audit_log(
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
        auth: AuthContext = Depends(require_role("admin")),
    ) -> dict:
        _audit(auth, "api_read", "audit_log", None)
        entries = resolved_repository.audit_entries()
        return {"limit": limit, "entries": entries[-limit:]}

    return app


app = create_app()
