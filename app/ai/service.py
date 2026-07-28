from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.ai.config import AISettings
from app.ai.db import AIRepository
from app.ai.prompts import build_page_prompt, build_vehicle_page_prompt
from app.ai.providers import VisionProvider
from app.ai.rules import evaluate_document
from app.ai.schemas import (
    DocumentSnapshot,
    EmployeeSnapshot,
    RuleIssue,
    VehicleDocumentSnapshot,
    VehicleSnapshot,
)
from app.ai.vision import iter_document_pages
from app.constants.doc_catalog import DOC_CATALOG

EXTRACTION_FIELDS = (
    "document_type",
    "full_name",
    "iin",
    "issue_date",
    "expiry_date",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DocumentAIService:
    def __init__(
        self,
        *,
        settings: AISettings,
        repository: AIRepository,
        provider: VisionProvider,
    ):
        self.settings = settings
        self.repository = repository
        self.provider = provider

    def _store_issue(
        self,
        *,
        run_id: int | None,
        employee_id: str,
        document_id: str | None,
        source_file: str,
        issue: RuleIssue,
    ) -> int:
        finding_id = self.repository.add_finding(
            run_id=run_id,
            employee_id=employee_id,
            document_id=document_id,
            issue_code=issue.issue_code,
            field_name=issue.field_name,
            found_value=issue.found_value,
            confidence=issue.confidence,
            source_file=source_file,
            page_number=issue.page_number,
            severity=issue.severity,
            message=issue.message,
        )
        self.repository.enqueue_finding(
            finding_id=finding_id,
            employee_id=employee_id,
            document_id=document_id,
            reason_code=issue.issue_code,
            priority=issue.severity,
        )
        return finding_id

    def check_completeness(self, employee: EmployeeSnapshot, *, persist: bool = True) -> dict[str, Any]:
        existing_codes = {document.document_code for document in employee.documents}
        missing = [code for code in self.settings.required_document_codes if code not in existing_codes]
        if persist:
            for code in missing:
                issue = RuleIssue(
                    issue_code="missing_required_document",
                    field_name="document_code",
                    found_value=None,
                    confidence=1,
                    page_number=None,
                    severity="high",
                    message=(f"Отсутствует обязательный документ: {DOC_CATALOG.get(code, code)}"),
                )
                self._store_issue(
                    run_id=None,
                    employee_id=employee.employee_id,
                    document_id=f"{employee.employee_id}:missing:{code}",
                    source_file="",
                    issue=issue,
                )
        return {
            "employee_id": employee.employee_id,
            "required_document_codes": list(self.settings.required_document_codes),
            "present_document_codes": sorted(existing_codes),
            "missing_documents": [
                {"document_code": code, "name": DOC_CATALOG.get(code, "Неизвестно")} for code in missing
            ],
            "is_complete": not missing,
            "findings": self.repository.employee_findings(employee.employee_id),
        }

    def analyze_document(self, employee: EmployeeSnapshot, document: DocumentSnapshot) -> dict[str, Any]:
        source_file = str(document.source_path)
        run_id = self.repository.create_run(
            employee_id=employee.employee_id,
            document_id=document.document_id,
            source_file=source_file,
            provider=self.settings.provider,
            model=self.settings.model,
        )
        try:
            duplicates = self.repository.register_fingerprint(
                employee_id=employee.employee_id,
                document_id=document.document_id,
                source_file=source_file,
                sha256=file_sha256(document.source_path),
            )
            for duplicate in duplicates:
                belongs_to_other = duplicate["employee_id"] != employee.employee_id
                issue = RuleIssue(
                    issue_code=("other_employee_document" if belongs_to_other else "duplicate_document"),
                    field_name="sha256",
                    found_value=duplicate["document_id"],
                    confidence=1,
                    page_number=None,
                    severity="high" if belongs_to_other else "medium",
                    message=(
                        "Этот файл уже зарегистрирован у другого сотрудника"
                        if belongs_to_other
                        else "Одинаковый файл используется как несколько документов"
                    ),
                )
                self._store_issue(
                    run_id=run_id,
                    employee_id=employee.employee_id,
                    document_id=document.document_id,
                    source_file=source_file,
                    issue=issue,
                )

            page_results = []
            for page in iter_document_pages(document.source_path, self.settings):
                extraction = self.provider.extract(
                    image_bytes=page.image_bytes,
                    prompt=build_page_prompt(document.document_code),
                )
                page_results.append((page.number, extraction))
                for field_name in EXTRACTION_FIELDS:
                    field = getattr(extraction, field_name)
                    self.repository.add_extraction(
                        run_id=run_id,
                        employee_id=employee.employee_id,
                        document_id=document.document_id,
                        field_name=field_name,
                        found_value=field.value,
                        confidence=field.confidence,
                        source_file=source_file,
                        page_number=page.number,
                    )

            issues = evaluate_document(
                expected_full_name=employee.full_name,
                expected_iin=employee.iin,
                document_code=document.document_code,
                dated_document_codes=self.settings.dated_document_codes,
                pages=page_results,
                confidence_threshold=self.settings.confidence_threshold,
            )
            for issue in issues:
                self._store_issue(
                    run_id=run_id,
                    employee_id=employee.employee_id,
                    document_id=document.document_id,
                    source_file=source_file,
                    issue=issue,
                )
            self.repository.finish_run(run_id, "completed")
            return self.repository.document_analysis(document.document_id)
        except Exception as exc:
            self.repository.finish_run(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
            raise

    def analyze_employee(self, employee: EmployeeSnapshot) -> dict[str, Any]:
        completeness = self.check_completeness(employee)
        analyzed = []
        failed = []
        for document in employee.documents:
            try:
                self.analyze_document(employee, document)
                analyzed.append(document.document_id)
            except Exception as exc:  # noqa: BLE001 - один файл не останавливает пакет
                failed.append(
                    {
                        "document_id": document.document_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return {
            "employee_id": employee.employee_id,
            "completeness": completeness,
            "analyzed_documents": analyzed,
            "failed_documents": failed,
        }


class VehicleAIService:
    """Анализ документов транспортных средств. Shadow mode: production-данные не меняются."""

    def __init__(
        self,
        *,
        settings: AISettings,
        repository: AIRepository,
        provider: VisionProvider,
    ):
        self.settings = settings
        self.repository = repository
        self.provider = provider

    def _store_issue(
        self,
        *,
        run_id: int | None,
        vehicle_id: str,
        document_id: str | None,
        source_file: str,
        issue: RuleIssue,
    ) -> None:
        finding_id = self.repository.add_vehicle_finding(
            run_id=run_id,
            vehicle_id=vehicle_id,
            document_id=document_id,
            issue_code=issue.issue_code,
            field_name=issue.field_name,
            found_value=issue.found_value,
            confidence=issue.confidence,
            source_file=source_file,
            page_number=issue.page_number,
            severity=issue.severity,
            message=issue.message,
        )
        self.repository.enqueue_vehicle_finding(
            finding_id=finding_id,
            vehicle_id=vehicle_id,
            document_id=document_id,
            reason_code=issue.issue_code,
            priority=issue.severity,
        )

    def analyze_vehicle_document(
        self, vehicle: VehicleSnapshot, document: VehicleDocumentSnapshot
    ) -> dict[str, Any]:
        from app.ai.vehicles import evaluate_vehicle_document

        source_file = str(document.source_path)
        run_id = self.repository.create_vehicle_run(
            vehicle_id=vehicle.vehicle_id,
            document_id=document.document_id,
            document_code=document.document_code,
            source_file=source_file,
            provider=self.settings.provider,
            model=self.settings.model,
        )
        try:
            sha256 = file_sha256(document.source_path)
            duplicates = self.repository.register_fingerprint(
                employee_id=f"vehicle:{vehicle.vehicle_id}",
                document_id=document.document_id,
                source_file=source_file,
                sha256=sha256,
            )
            for duplicate in duplicates:
                belongs_to_other = duplicate["employee_id"] != f"vehicle:{vehicle.vehicle_id}"
                issue = RuleIssue(
                    issue_code="other_vehicle_document" if belongs_to_other else "duplicate_document",
                    field_name="sha256",
                    found_value=duplicate["document_id"],
                    confidence=1.0,
                    page_number=None,
                    severity="high" if belongs_to_other else "medium",
                    message=(
                        "Файл уже зарегистрирован у другого ТС" if belongs_to_other else "Дубликат документа"
                    ),
                )
                self._store_issue(
                    run_id=run_id,
                    vehicle_id=vehicle.vehicle_id,
                    document_id=document.document_id,
                    source_file=source_file,
                    issue=issue,
                )

            page_results = []
            prompt = build_vehicle_page_prompt(document.document_code)
            for page in iter_document_pages(document.source_path, self.settings):
                extraction = self.provider.extract_vehicle(
                    image_bytes=page.image_bytes,
                    prompt=prompt,
                )
                page_results.append((page.number, extraction))

            issues = evaluate_vehicle_document(
                vehicle=vehicle,
                document=document,
                pages=page_results,
                confidence_threshold=self.settings.confidence_threshold,
            )
            for issue in issues:
                self._store_issue(
                    run_id=run_id,
                    vehicle_id=vehicle.vehicle_id,
                    document_id=document.document_id,
                    source_file=source_file,
                    issue=issue,
                )
            self.repository.finish_vehicle_run(run_id, "completed")
            return self.repository.vehicle_analysis(vehicle.vehicle_id)
        except Exception as exc:
            self.repository.finish_vehicle_run(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
            raise
