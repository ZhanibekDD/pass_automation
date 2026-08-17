"""Tests for app.ai.rules — date parser and evaluation logic.

Covers all examples from the analysis report for employee 40:
  - Russian text dates with guillemets and suffixes
  - Two-digit year expansion
  - Date ranges
  - Year-only (partial) dates
  - Field bleed detection
  - fio_mismatch logic (all-pages, not just best-field)
  - expired_document / partial_expiry_date / unreadable_expiry_date
"""
from __future__ import annotations

from datetime import date

import pytest

from app.ai.rules import (
    evaluate_document,
    parse_document_date,
    parse_document_date_rich,
)
from app.ai.schemas import ExtractedField, PageExtraction

# ── helpers ────────────────────────────────────────────────────────────────────


def page(
    *,
    fio: str | None = None,
    iin: str | None = None,
    issue_date: str | None = None,
    expiry_date: str | None = None,
    doc_type: str | None = "Удостоверение",
    confidence: float = 0.95,
) -> PageExtraction:
    return PageExtraction(
        document_type=ExtractedField(value=doc_type, confidence=confidence),
        full_name=ExtractedField(value=fio, confidence=confidence if fio else 0),
        iin=ExtractedField(value=iin, confidence=confidence if iin else 0),
        issue_date=ExtractedField(value=issue_date, confidence=confidence if issue_date else 0),
        expiry_date=ExtractedField(value=expiry_date, confidence=confidence if expiry_date else 0),
    )


def issue_codes(pages_list, *, expected_fio="Иванов Иван Иванович", expected_iin="1111"):
    return {
        i.issue_code
        for i in evaluate_document(
            expected_full_name=expected_fio,
            expected_iin=expected_iin,
            document_code=6,
            dated_document_codes=(6,),
            pages=[(n + 1, p) for n, p in enumerate(pages_list)],
            confidence_threshold=0.75,
            today=date(2026, 7, 29),
        )
    }


# ── parse_document_date_rich — exact Russian dates ─────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("31 декабря 2026 года", date(2026, 12, 31)),
    ("01 августа 2025 г", date(2025, 8, 1)),
    ("14 января 2025 г.", date(2025, 1, 14)),
    ("2 марта 2022 года", date(2022, 3, 2)),
    ("07 апреля 2022 года", date(2022, 4, 7)),
    ("31 мая 2025 г.", date(2025, 5, 31)),
    ("15 июня 2024 года", date(2024, 6, 15)),
    ("10 июля 2023 г", date(2023, 7, 10)),
    ("5 ноября 2022 года", date(2022, 11, 5)),
])
def test_russian_text_dates(raw, expected):
    result = parse_document_date_rich(raw)
    assert result.value == expected
    assert not result.is_partial
    assert not result.is_range


def test_guillemet_wrapped_date():
    """«14» января 2025 г. — guillemets must be stripped before parsing."""
    result = parse_document_date_rich("«14» января 2025 г.")
    assert result.value == date(2025, 1, 14)
    assert not result.is_partial


def test_field_bleed_stripped_date():
    """«14» января 2025 г. № — trailing № is field bleed, date still parsed."""
    result = parse_document_date_rich("«14» января 2025 г. №")
    assert result.value == date(2025, 1, 14)
    assert result.has_field_bleed


def test_field_bleed_stripped_date_with_number():
    """«14» января 2025 г. №20 — trailing №+digits is field bleed."""
    result = parse_document_date_rich("«14» января 2025 г. №20")
    assert result.value == date(2025, 1, 14)
    assert result.has_field_bleed


# ── two-digit year expansion ───────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("26.02.26", date(2026, 2, 26)),   # 26 < 50 → 2026
    ("01.01.49", date(2049, 1, 1)),    # 49 < 50 → 2049
    ("01.01.50", date(1950, 1, 1)),    # 50 >= 50 → 1950
    ("31.12.99", date(1999, 12, 31)),  # 99 >= 50 → 1999
    ("15.06.00", date(2000, 6, 15)),   # 00 < 50 → 2000
])
def test_two_digit_year_expansion(raw, expected):
    result = parse_document_date_rich(raw)
    assert result.value == expected, f"{raw!r} → {result.value}, expected {expected}"


# ── date ranges ────────────────────────────────────────────────────────────────


def test_date_range_extracts_end_date():
    """с 02 марта 2022 года по 07 апреля 2022 года → end date Apr 7 2022."""
    result = parse_document_date_rich("с 02 марта 2022 года по 07 апреля 2022 года")
    assert result.value == date(2022, 4, 7)
    assert result.is_range
    assert not result.is_partial


def test_date_range_with_dash_separator():
    result = parse_document_date_rich("01 января 2025 года - 31 декабря 2025 года")
    assert result.value == date(2025, 12, 31)
    assert result.is_range


# ── year-only (partial) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    "2024г",
    "2024г.",
    "2025 г.",
    "2025 года",
    "2026г",
])
def test_year_only_is_partial(raw):
    result = parse_document_date_rich(raw)
    assert result.is_partial, f"{raw!r} should be partial"
    assert result.value is not None
    assert result.value.year == int(raw[:4])


def test_parse_document_date_returns_none_for_partial():
    """Backward-compat: parse_document_date returns None for partial dates."""
    assert parse_document_date("2024г") is None
    assert parse_document_date("2024 года") is None


# ── unreadable / genuinely unknown ────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    "16 ОТ 2024",      # "ОТ" is not a Russian month name
    "бессрочно",       # "indefinitely" — no date
    "не указан",       # "not specified"
    "",
    None,
])
def test_unreadable_dates(raw):
    result = parse_document_date_rich(raw)
    assert result.value is None, f"{raw!r} should be unreadable, got {result.value}"
    assert not result.is_partial


# ── standard numeric formats ───────────────────────────────────────────────────


def test_dot_separated_numeric():
    assert parse_document_date_rich("01.01.2025").value == date(2025, 1, 1)


def test_slash_separated_numeric():
    assert parse_document_date_rich("15/06/2024").value == date(2024, 6, 15)


def test_iso_format_yyyy_mm_dd():
    assert parse_document_date_rich("2025.01.31").value == date(2025, 1, 31)


# ── evaluate_document: fio_mismatch logic ─────────────────────────────────────


def test_fio_mismatch_when_no_page_matches():
    """fio_mismatch fires if no name on any page matches the employee."""
    codes = issue_codes([
        page(fio="Донской Д.А.", expiry_date="01.01.2030"),
    ])
    assert "fio_mismatch" in codes


def test_fio_no_mismatch_when_subject_on_page1():
    """fio_mismatch NOT fired when page 1 has the employee's name."""
    codes = issue_codes([
        page(fio="Иванов Иван Иванович", expiry_date="01.01.2030"),
    ])
    assert "fio_mismatch" not in codes


def test_fio_no_mismatch_when_employee_on_any_page():
    """fio_mismatch NOT fired when the employee appears on any page, even if a signatory
    has higher confidence on another page (preventing false positives in protocols)."""
    # Page 1: subject = employee (lower conf)
    # Page 2: signatory with different name (higher conf → becomes best_field)
    subj = page(fio="Иванов Иван Иванович", expiry_date="01.01.2030", confidence=0.80)
    signatory = page(fio="Донской Дмитрий Алексеевич", confidence=0.99)
    codes = issue_codes([subj, signatory])
    assert "fio_mismatch" not in codes


def test_fio_mismatch_when_only_signatories_present():
    """fio_mismatch fires when all extracted names belong to other people."""
    p1 = page(fio="Сидоров Сидор Сидорович", expiry_date="01.01.2030")
    p2 = page(fio="Петров Петр Петрович")
    codes = issue_codes([p1, p2])
    assert "fio_mismatch" in codes


# ── evaluate_document: expiry date issue codes ────────────────────────────────


def test_expired_document_fires_for_exact_past_date():
    codes = issue_codes([page(fio="Иванов Иван Иванович", expiry_date="01.01.2025")])
    assert "expired_document" in codes
    assert "unreadable_expiry_date" not in codes
    assert "partial_expiry_date" not in codes


def test_expired_document_fires_for_russian_past_date():
    """31 декабря 2024 года — parseable Russian date, expired."""
    codes = issue_codes([page(fio="Иванов Иван Иванович", expiry_date="31 декабря 2024 года")])
    assert "expired_document" in codes
    assert "unreadable_expiry_date" not in codes


def test_no_expiry_issue_for_future_date():
    codes = issue_codes([page(fio="Иванов Иван Иванович", expiry_date="01.01.2030")])
    assert "expired_document" not in codes
    assert "unreadable_expiry_date" not in codes
    assert "partial_expiry_date" not in codes


def test_partial_expiry_for_year_only():
    """2024г — partial date, operator must verify."""
    codes = issue_codes([page(fio="Иванов Иван Иванович", expiry_date="2025г")])
    assert "partial_expiry_date" in codes
    assert "expired_document" not in codes
    assert "unreadable_expiry_date" not in codes


def test_partial_expiry_for_date_range():
    """Date range → is_range=True → partial_expiry_date, not expired_document."""
    codes = issue_codes([
        page(
            fio="Иванов Иван Иванович",
            expiry_date="с 02 марта 2022 года по 07 апреля 2022 года",
        )
    ])
    assert "partial_expiry_date" in codes
    # Even though end date is in the past, operator must confirm the range interpretation
    assert "expired_document" not in codes


def test_unreadable_expiry_for_unknown_text():
    """16 ОТ 2024 — not parseable → unreadable_expiry_date, not expired_document."""
    codes = issue_codes([page(fio="Иванов Иван Иванович", expiry_date="16 ОТ 2024")])
    assert "unreadable_expiry_date" in codes
    assert "expired_document" not in codes
    assert "partial_expiry_date" not in codes


def test_no_false_unreadable_for_russian_date():
    """01 августа 2025 г — parseable Russian date, must NOT fire unreadable_expiry_date."""
    codes = issue_codes([page(fio="Иванов Иван Иванович", expiry_date="01 августа 2025 г")])
    assert "unreadable_expiry_date" not in codes


def test_no_false_unreadable_for_guillemet_date():
    """«14» января 2025 г. — parseable after stripping → no unreadable."""
    codes = issue_codes([page(fio="Иванов Иван Иванович", expiry_date="«14» января 2025 г.")])
    assert "unreadable_expiry_date" not in codes


def test_two_digit_year_not_unreadable():
    """26.02.26 — two-digit year parseable → no unreadable_expiry_date."""
    codes = issue_codes([page(fio="Иванов Иван Иванович", expiry_date="26.02.26")])
    assert "unreadable_expiry_date" not in codes
