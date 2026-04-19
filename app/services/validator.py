from pathlib import Path

from app.config import MAX_FILE_SIZE_BYTES
from app.constants.doc_catalog import DOC_CATALOG


class ValidationError(Exception):
    pass


def validate_pdf_file(path: Path) -> None:
    if not path.exists():
        raise ValidationError(f"Файл не найден: {path}")

    if path.suffix.lower() != ".pdf":
        raise ValidationError(f"Файл должен быть PDF: {path}")

    if path.stat().st_size > MAX_FILE_SIZE_BYTES:
        raise ValidationError(
            f"Файл превышает 1.8 МБ: {path} ({path.stat().st_size} байт)"
        )


def validate_doc_code(doc_code: int) -> None:
    if doc_code not in DOC_CATALOG:
        raise ValidationError(f"Неизвестный код документа: {doc_code}")


def validate_documents_map(documents: dict[int, Path]) -> None:
    seen_codes: set[int] = set()

    for doc_code, path in documents.items():
        if doc_code in seen_codes:
            raise ValidationError(f"Дублирующийся код документа: {doc_code}")

        validate_doc_code(doc_code)
        validate_pdf_file(path)
        seen_codes.add(doc_code)


def validate_common_docs(
    contractor_agreement: Path | None,
    subcontract_agreement: Path | None,
    signed_application_scan: Path | None,
) -> None:
    for path in [contractor_agreement, subcontract_agreement, signed_application_scan]:
        if path is not None:
            validate_pdf_file(path)
