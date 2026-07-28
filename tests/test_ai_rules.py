from datetime import date

from app.ai.rules import evaluate_document, normalize_fio, normalize_iin
from app.ai.schemas import ExtractedField, PageExtraction


def extraction(
    *,
    fio: str | None = "Иванов Иван Иванович",
    iin: str | None = "1111",
    issue_date: str | None = "01.01.2020",
    expiry_date: str | None = "01.01.2025",
    confidence: float = 0.95,
) -> PageExtraction:
    return PageExtraction(
        document_type=ExtractedField(value="Удостоверение", confidence=confidence),
        full_name=ExtractedField(value=fio, confidence=confidence if fio else 0),
        iin=ExtractedField(value=iin, confidence=confidence if iin else 0),
        issue_date=ExtractedField(
            value=issue_date, confidence=confidence if issue_date else 0
        ),
        expiry_date=ExtractedField(
            value=expiry_date, confidence=confidence if expiry_date else 0
        ),
    )


def test_normalization_is_order_and_format_tolerant() -> None:
    assert normalize_fio("Иванов  Иван Иванович") == normalize_fio(
        "Иван Иванович ИВАНОВ"
    )
    assert normalize_iin("11 11") == "1111"


def test_detects_expired_and_other_employee_document() -> None:
    issues = evaluate_document(
        expected_full_name="Петров Петр Петрович",
        expected_iin="2222",
        document_code=6,
        dated_document_codes=(6,),
        pages=[(1, extraction())],
        confidence_threshold=0.75,
        today=date(2026, 7, 28),
    )
    codes = {issue.issue_code for issue in issues}
    assert {
        "fio_mismatch",
        "iin_mismatch",
        "other_employee_document",
        "expired_document",
    }.issubset(codes)


def test_absent_dates_remain_null_and_go_to_review() -> None:
    issues = evaluate_document(
        expected_full_name="Иванов Иван Иванович",
        expected_iin="1111",
        document_code=7,
        dated_document_codes=(7,),
        pages=[(1, extraction(issue_date=None, expiry_date=None))],
        confidence_threshold=0.75,
        today=date(2026, 7, 28),
    )
    empty = next(issue for issue in issues if issue.issue_code == "empty_dates")
    assert empty.found_value is None
    assert empty.confidence == 0


def test_low_confidence_value_is_not_silently_accepted() -> None:
    issues = evaluate_document(
        expected_full_name="Иванов Иван Иванович",
        expected_iin="1111",
        document_code=6,
        dated_document_codes=(),
        pages=[(1, extraction(confidence=0.4))],
        confidence_threshold=0.75,
    )
    assert any(issue.issue_code == "low_confidence" for issue in issues)
