from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status

from app.ai.adapters import load_package_snapshot
from app.ai.config import AISettings
from app.ai.db import AIRepository
from app.ai.providers import VisionProvider, create_provider
from app.ai.schemas import EmployeeSnapshot, ReviewUpdate
from app.ai.service import DocumentAIService
from app.services.input_loader import InputLoaderError


def create_app(
    *,
    settings: AISettings | None = None,
    repository: AIRepository | None = None,
    provider: VisionProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or AISettings.from_env()
    resolved_repository = repository or AIRepository(resolved_settings.database_path)
    resolved_repository.initialize()
    resolved_provider = provider or create_provider(resolved_settings)
    service = DocumentAIService(
        settings=resolved_settings,
        repository=resolved_repository,
        provider=resolved_provider,
    )

    app = FastAPI(
        title="Pass Docs Local AI",
        version="0.1.0",
        description=(
            "Локальный sidecar для анализа документов. Production-БД не изменяется."
        ),
    )
    app.state.settings = resolved_settings
    app.state.repository = resolved_repository
    app.state.provider = resolved_provider
    app.state.service = service

    def require_api_key(
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = resolved_settings.api_key
        if expected and (
            x_api_key is None or not hmac.compare_digest(x_api_key, expected)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Некорректный X-API-Key",
            )

    def load_employee() -> EmployeeSnapshot:
        try:
            return load_package_snapshot(resolved_settings.input_json_path)
        except (FileNotFoundError, InputLoaderError, ValueError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Источник сотрудников недоступен: {type(exc).__name__}",
            ) from exc

    def audit_read(
        action: str,
        entity_type: str,
        entity_id: str | None,
        operator_id: str,
    ) -> None:
        resolved_repository.audit(
            actor_type="user",
            actor_id=operator_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
        )

    employee_dependency = Depends(load_employee)

    @app.get("/api/ai/health")
    def health() -> dict:
        database = resolved_repository.health()
        model = resolved_provider.health()
        input_status = (
            "ok" if resolved_settings.input_json_path.is_file() else "unavailable"
        )
        overall = (
            "ok"
            if database["status"] == "ok"
            and (not resolved_settings.enabled or model["status"] == "ok")
            else "degraded"
        )
        return {
            "status": overall,
            "enabled": resolved_settings.enabled,
            "database": database,
            "model": model,
            "input_adapter": input_status,
            "read_only_production": True,
        }

    @app.get("/api/ai/summary", dependencies=[Depends(require_api_key)])
    def summary(
        x_operator_id: Annotated[str, Header()] = "anonymous",
    ) -> dict:
        audit_read("api_read", "summary", None, x_operator_id)
        return resolved_repository.summary()

    @app.get("/api/ai/review-queue", dependencies=[Depends(require_api_key)])
    def review_queue(
        queue_status: Annotated[
            str, Query(alias="status", pattern="^(pending|confirmed|rejected)$")
        ] = "pending",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        x_operator_id: Annotated[str, Header()] = "anonymous",
    ) -> dict:
        audit_read("api_read", "review_queue", queue_status, x_operator_id)
        items = resolved_repository.list_review_queue(
            status=queue_status, limit=limit, offset=offset
        )
        return {
            "status": queue_status,
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    @app.patch(
        "/api/ai/review-queue/{item_id}",
        dependencies=[Depends(require_api_key)],
    )
    def update_review_queue(
        item_id: int,
        payload: ReviewUpdate,
        x_operator_id: Annotated[str, Header()],
    ) -> dict:
        item = resolved_repository.update_review(
            item_id=item_id,
            status=payload.status,
            operator_id=x_operator_id,
            comment=payload.comment,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Элемент очереди не найден")
        return item

    @app.get(
        "/api/ai/employees/{employee_id}/completeness",
        dependencies=[Depends(require_api_key)],
    )
    def employee_completeness(
        employee_id: str,
        employee: EmployeeSnapshot = employee_dependency,
        x_operator_id: Annotated[str, Header()] = "anonymous",
    ) -> dict:
        if employee.employee_id != employee_id:
            raise HTTPException(status_code=404, detail="Сотрудник не найден")
        audit_read("api_read", "employee", employee_id, x_operator_id)
        return service.check_completeness(employee, persist=False)

    @app.get(
        "/api/ai/documents/{document_id}/analysis",
        dependencies=[Depends(require_api_key)],
    )
    def document_analysis(
        document_id: str,
        employee: EmployeeSnapshot = employee_dependency,
        x_operator_id: Annotated[str, Header()] = "anonymous",
    ) -> dict:
        known_document_ids = {item.document_id for item in employee.documents}
        if document_id not in known_document_ids:
            raise HTTPException(status_code=404, detail="Документ не найден")
        audit_read("api_read", "document", document_id, x_operator_id)
        return resolved_repository.document_analysis(document_id)

    return app


app = create_app()
