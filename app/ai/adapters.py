from __future__ import annotations

from pathlib import Path

from app.ai.schemas import DocumentSnapshot, EmployeeSnapshot
from app.services.input_loader import load_package_input


def load_package_snapshot(json_path: Path) -> EmployeeSnapshot:
    """
    Адаптер текущего JSON-формата к AI-модулю.

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
