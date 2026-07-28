"""Адаптеры источников данных о сотрудниках.

Два адаптера (оба read-only, production-БД не изменяется):
  1. JsonFileAdapter   — читает из package_input.json (текущий режим работы CLI)
  2. DASHttpAdapter    — читает из read-only API сервера DAS (pilot)

Переключение через env:
  AI_DATA_ADAPTER=json   (по умолчанию) — package_input.json
  AI_DATA_ADAPTER=das    — DAS HTTP API

Для DAS также нужны:
  AI_DAS_BASE_URL=http://localhost:8000
  AI_DAS_TOKEN=<токен, задан как AI_INTERNAL_TOKEN на сервере DAS>
"""

from __future__ import annotations

import os
from pathlib import Path

from app.ai.schemas import DocumentSnapshot, EmployeeSnapshot
from app.services.input_loader import load_package_input


def load_package_snapshot(json_path: Path) -> EmployeeSnapshot:
    """JSON file adapter: существующий формат package_input.json.

    Используется в CLI-режиме (pass_automation) и в тестах.
    Production-БД не читается и не изменяется. Идентификатор документа составной:
    ``{employee_index}:{document_code}``.
    """
    package = load_package_input(json_path)
    documents = tuple(
        DocumentSnapshot(
            document_id=f"{package.employee_index}:{document_code}",
            document_code=document_code,
            source_path=source_path,
        )
        for document_code, source_path in sorted(package.documents.items())
    )
    return EmployeeSnapshot(
        employee_id=str(package.employee_index),
        full_name=package.fio,
        iin=package.iin,
        documents=documents,
    )


class DASHttpAdapter:
    """Read-only адаптер для DAS HTTP API.

    Требует развёртывания deploy/das/adminpanel_ai_api.py на сервере DAS
    и переменных окружения AI_DAS_BASE_URL + AI_DAS_TOKEN.

    Персональные данные (ИИН) передаются только по внутреннему каналу,
    не попадают в логи (маскирование происходит на уровне AI rules).
    """

    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        if not base_url:
            raise ValueError("AI_DAS_BASE_URL не задан")
        if not token:
            raise ValueError("AI_DAS_TOKEN не задан")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Ai-Token": self._token}

    def load_employee(self, employee_id: str | int) -> EmployeeSnapshot:
        """Загружает одного сотрудника из DAS API. Только GET, production-БД не изменяется."""
        import httpx

        url = f"{self._base_url}/api/ai-internal/employee/{employee_id}/"
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(url, headers=self._headers())
        if response.status_code == 401:
            raise PermissionError("DAS AI token неверен (401)")
        if response.status_code == 404:
            raise FileNotFoundError(f"Сотрудник {employee_id} не найден в DAS")
        response.raise_for_status()

        data = response.json()
        documents = tuple(
            DocumentSnapshot(
                document_id=doc["document_id"],
                document_code=int(doc["document_code"]),
                source_path=Path(doc["source_path"]),
            )
            for doc in data.get("documents", [])
            if doc.get("source_path")
        )
        return EmployeeSnapshot(
            employee_id=str(data["employee_id"]),
            full_name=data["full_name"],
            iin=data.get("iin") or None,
            documents=documents,
        )

    def download_document_file(self, document_id: str, *, dest_dir: Path) -> Path:
        """Скачивает файл документа из DAS в dest_dir.

        Caller отвечает за удаление файла (используйте tempfile.TemporaryDirectory).
        SHA-256 вычисляется в DocumentAIService.file_sha256 после скачивания.
        """
        import httpx

        url = f"{self._base_url}/api/ai-internal/documents/{document_id}/file/"
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(url, headers=self._headers())
        if response.status_code == 401:
            raise PermissionError("DAS AI token неверен (401)")
        if response.status_code == 404:
            raise FileNotFoundError(f"Документ {document_id} не найден в DAS")
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type:
            ext = ".pdf"
        elif "jpeg" in content_type or "jpg" in content_type:
            ext = ".jpg"
        elif "png" in content_type:
            ext = ".png"
        else:
            ext = ".bin"

        safe_id = document_id.replace("/", "_").replace(":", "_").replace("..", "_")
        dest_file = dest_dir / f"{safe_id}{ext}"
        dest_file.write_bytes(response.content)
        return dest_file

    def list_employee_ids(self, *, company: str = "", limit: int = 100, offset: int = 0) -> list[int]:
        """Возвращает список ID активных сотрудников из DAS."""
        import httpx

        url = f"{self._base_url}/api/ai-internal/employees/"
        params: dict[str, str | int] = {"limit": limit, "offset": offset}
        if company:
            params["company"] = company
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(url, headers=self._headers(), params=params)
        if response.status_code == 401:
            raise PermissionError("DAS AI token неверен (401)")
        response.raise_for_status()
        return [row["id"] for row in response.json().get("employees", [])]


def get_das_adapter() -> DASHttpAdapter | None:
    """Фабрика: возвращает DASHttpAdapter если AI_DATA_ADAPTER=das, иначе None."""
    adapter_type = os.getenv("AI_DATA_ADAPTER", "json").strip().lower()
    if adapter_type != "das":
        return None
    return DASHttpAdapter(
        base_url=os.getenv("AI_DAS_BASE_URL", "").strip(),
        token=os.getenv("AI_DAS_TOKEN", "").strip(),
    )
