"""FastAPI приложение Pass Docs Local AI.

Аутентификация: X-Api-Key + роль (viewer/operator/reviewer/admin).
Production-БД не изменяется ни при каких условиях.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status

from app.ai.adapters import load_package_snapshot
from app.ai.auth import AuthContext, TokenRegistry, require_role
from app.ai.config import AISettings
from app.ai.db import AIRepository
from app.ai.providers import VisionProvider, create_provider
from app.ai.schemas import EmployeeSnapshot, ReviewUpdate
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
        version="0.2.0",
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

    def _load_employee() -> EmployeeSnapshot:
        try:
            return load_package_snapshot(resolved_settings.input_json_path)
        except (FileNotFoundError, InputLoaderError, ValueError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Источник сотрудников недоступен: {type(exc).__name__}",
            ) from exc

    # ------------------------------------------------------------------ health (открыт)

    @app.get("/api/ai/health")
    def health() -> dict:
        database = resolved_repository.health()
        model = resolved_provider.health()
        input_status = "ok" if resolved_settings.input_json_path.is_file() else "unavailable"
        overall = (
            "ok"
            if database["status"] == "ok" and (not resolved_settings.enabled or model["status"] == "ok")
            else "degraded"
        )
        return {
            "status": overall,
            "enabled": resolved_settings.enabled,
            "database": database,
            "model": model,
            "input_adapter": input_status,
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
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Элемент очереди не найден")
        _audit(
            auth,
            f"review_{payload.status}",
            "review_item",
            str(item_id),
            {"comment": payload.comment},
        )
        return item

    @app.get("/api/ai/employees/{employee_id}/completeness")
    def employee_completeness(
        employee_id: str,
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        employee = _load_employee()
        if employee.employee_id != employee_id:
            raise HTTPException(status_code=404, detail="Сотрудник не найден")
        _audit(auth, "api_read", "employee", employee_id)
        return service.check_completeness(employee, persist=False)

    @app.get("/api/ai/documents/{document_id}/analysis")
    def document_analysis(
        document_id: str,
        auth: AuthContext = Depends(require_role("viewer")),
    ) -> dict:
        employee = _load_employee()
        known_document_ids = {item.document_id for item in employee.documents}
        if document_id not in known_document_ids:
            raise HTTPException(status_code=404, detail="Документ не найден")
        _audit(auth, "api_read", "document", document_id)
        return resolved_repository.document_analysis(document_id)

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
        from app.ai.db import utc_now as _utc_now

        with resolved_repository._connect() as conn:
            row = conn.execute("SELECT * FROM ai_vehicle_review_queue WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Элемент очереди не найден")
            conn.execute(
                """
                UPDATE ai_vehicle_review_queue
                SET status = ?, operator_id = ?, operator_comment = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (
                    payload.status,
                    auth.operator_id,
                    payload.comment,
                    _utc_now(),
                    item_id,
                ),
            )
            conn.execute(
                "UPDATE ai_vehicle_findings SET operator_status = ? WHERE id = ?",
                (payload.status, row["entity_id"]),
            )
            updated = conn.execute(
                "SELECT * FROM ai_vehicle_review_queue WHERE id = ?", (item_id,)
            ).fetchone()
        _audit(
            auth,
            f"vehicle_review_{payload.status}",
            "vehicle_review_item",
            str(item_id),
            {"comment": payload.comment},
        )
        return dict(updated)

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
