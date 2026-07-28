from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime

from app.ai.schemas import PageExtraction, RuleIssue


def normalize_fio(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    tokens = re.findall(r"[^\W\d_]+", value.casefold(), flags=re.UNICODE)
    return tuple(sorted(tokens))


def normalize_iin(value: str | None) -> str:
    return "" if not value else "".join(ch for ch in value if ch.isdigit())


def parse_document_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip().replace("/", ".").replace("-", ".")
    parts = cleaned.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    try:
        if len(parts[0]) == 4:
            year, month, day = map(int, parts)
        else:
            day, month, year = map(int, parts)
        return date(year, month, day)
    except ValueError:
        return None


def _best_field(
    pages: Iterable[tuple[int, PageExtraction]], field_name: str
) -> tuple[int | None, str | None, float]:
    candidates = []
    for page_number, extraction in pages:
        field = getattr(extraction, field_name)
        if field.value is not None:
            candidates.append((field.confidence, page_number, field.value))
    if not candidates:
        return None, None, 0
    confidence, page_number, value = max(candidates)
    return page_number, value, confidence


def evaluate_document(
    *,
    expected_full_name: str,
    expected_iin: str | None,
    document_code: int,
    dated_document_codes: tuple[int, ...],
    pages: list[tuple[int, PageExtraction]],
    confidence_threshold: float,
    today: date | None = None,
) -> list[RuleIssue]:
    now = today or datetime.now(UTC).date()
    issues: list[RuleIssue] = []

    page_fio, found_fio, fio_conf = _best_field(pages, "full_name")
    page_iin, found_iin, iin_conf = _best_field(pages, "iin")
    _, issue_date, _ = _best_field(pages, "issue_date")
    page_expiry, expiry_date, expiry_conf = _best_field(pages, "expiry_date")

    fio_mismatch = bool(
        found_fio
        and expected_full_name
        and normalize_fio(found_fio) != normalize_fio(expected_full_name)
    )
    if fio_mismatch:
        issues.append(
            RuleIssue(
                issue_code="fio_mismatch",
                field_name="full_name",
                found_value=found_fio,
                confidence=fio_conf,
                page_number=page_fio,
                severity="high",
                message="ФИО в документе не совпадает с ФИО сотрудника",
            )
        )

    expected_iin_norm = normalize_iin(expected_iin)
    found_iin_norm = normalize_iin(found_iin)
    iin_mismatch = bool(
        expected_iin_norm and found_iin_norm and expected_iin_norm != found_iin_norm
    )
    if iin_mismatch:
        issues.append(
            RuleIssue(
                issue_code="iin_mismatch",
                field_name="iin",
                found_value=found_iin,
                confidence=iin_conf,
                page_number=page_iin,
                severity="high",
                message="ИИН в документе не совпадает с ИИН сотрудника",
            )
        )

    if fio_mismatch and iin_mismatch:
        issues.append(
            RuleIssue(
                issue_code="other_employee_document",
                field_name=None,
                found_value=found_fio,
                confidence=min(fio_conf, iin_conf),
                page_number=page_fio or page_iin,
                severity="high",
                message="ФИО и ИИН указывают, что документ может принадлежать другому сотруднику",
            )
        )

    if document_code in dated_document_codes and not issue_date and not expiry_date:
        issues.append(
            RuleIssue(
                issue_code="empty_dates",
                field_name="issue_date,expiry_date",
                found_value=None,
                confidence=0,
                page_number=None,
                severity="medium",
                message="На документе не найдены дата выдачи и срок действия",
            )
        )

    if expiry_date:
        parsed_expiry = parse_document_date(expiry_date)
        if parsed_expiry is None:
            issues.append(
                RuleIssue(
                    issue_code="unreadable_expiry_date",
                    field_name="expiry_date",
                    found_value=expiry_date,
                    confidence=expiry_conf,
                    page_number=page_expiry,
                    severity="medium",
                    message="Срок действия найден, но дата не распознана однозначно",
                )
            )
        elif parsed_expiry < now:
            issues.append(
                RuleIssue(
                    issue_code="expired_document",
                    field_name="expiry_date",
                    found_value=expiry_date,
                    confidence=expiry_conf,
                    page_number=page_expiry,
                    severity="high",
                    message="Срок действия документа истёк",
                )
            )

    for field_name in (
        "document_type",
        "full_name",
        "iin",
        "issue_date",
        "expiry_date",
    ):
        page_number, value, confidence = _best_field(pages, field_name)
        if value is not None and confidence < confidence_threshold:
            issues.append(
                RuleIssue(
                    issue_code="low_confidence",
                    field_name=field_name,
                    found_value=value,
                    confidence=confidence,
                    page_number=page_number,
                    severity="medium",
                    message=f"Поле {field_name} требует ручной проверки",
                )
            )

    return issues
