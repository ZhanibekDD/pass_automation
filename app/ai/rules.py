from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.ai.schemas import PageExtraction, RuleIssue


def normalize_fio(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    tokens = re.findall(r"[^\W\d_]+", value.casefold(), flags=re.UNICODE)
    return tuple(sorted(tokens))


def normalize_iin(value: str | None) -> str:
    return "" if not value else "".join(ch for ch in value if ch.isdigit())


# ── Russian date parser ─────────────────────────────────────────────────────────

# Stems map to month numbers; covers declined forms (января/февраля/марта etc.)
_MONTHS_RU: dict[str, int] = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "мая": 5, "май": 5, "июн": 6, "июл": 7,
    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

_MONTH_PAT = (
    r"январ[яье]?|феврал[яье]?|март[аеа]?|апрел[яье]?|ма[йя]|июн[яье]?|июл[яье]?|"
    r"август[аеа]?|сентябр[яье]?|октябр[яье]?|ноябр[яье]?|декабр[яье]?"
)

# Matches «DD» MMMM YYYY or DD MMMM YYYY, with optional г./года/лет suffix
_RU_DATE_RE = re.compile(
    r"[«\"]?(\d{1,2})[»\"]?\s+(" + _MONTH_PAT + r")\s+(\d{2,4})"
    r"(?:\s*(?:год[ауе]?|лет[а]?|г\.?))?",
    re.IGNORECASE | re.UNICODE,
)

# Date range: [с] <date1> [по|до|—|–|-] <date2>  — captures end date as group 2
_RU_RANGE_RE = re.compile(
    r"(?:с\s+)?"
    r"(\d{1,2}\s+(?:" + _MONTH_PAT + r")\s+\d{2,4}(?:\s*(?:год[ауе]?|г\.?))?)"
    r"\s+(?:по|до|—|–|-)\s+"
    r"(\d{1,2}\s+(?:" + _MONTH_PAT + r")\s+\d{2,4}(?:\s*(?:год[ауе]?|г\.?))?)",
    re.IGNORECASE | re.UNICODE,
)

# Year-only patterns: 2024г, 2024г., 2024 г., 2024 года
_YEAR_ONLY_RE = re.compile(r"^(\d{4})\s*(?:год[ауе]?|г\.?)?$", re.UNICODE)

# Numeric DD.MM.YY[YY] or YYYY.MM.DD (any of . / - separators)
_NUMERIC_RE = re.compile(r"^(\d{1,4})[./\-](\d{1,2})[./\-](\d{2,4})$")

# Trailing field bleed: space + № (optionally followed by alphanumerics)
_BLEED_RE = re.compile(r"\s+[№N]\s*\w*\s*$", re.IGNORECASE | re.UNICODE)


@dataclass(frozen=True)
class ParsedDate:
    """Result of date parsing with quality metadata."""

    value: date | None              # None = completely unrecognized
    is_partial: bool = False        # only year (or year+month) extracted
    is_range: bool = False          # end-date extracted from a date range
    has_field_bleed: bool = False   # trailing junk detected (e.g. №)


def _expand_year(year: int) -> int:
    """Two-digit year expansion rule: ≥50 → 19xx, <50 → 20xx."""
    if year < 100:
        return (1900 + year) if year >= 50 else (2000 + year)
    return year


def _parse_ru_month(word: str) -> int | None:
    lower = word.lower()
    for stem, num in _MONTHS_RU.items():
        if lower.startswith(stem):
            return num
    return None


def _try_ru_date(text: str) -> date | None:
    m = _RU_DATE_RE.search(text)
    if not m:
        return None
    day = int(m.group(1))
    month = _parse_ru_month(m.group(2))
    year = _expand_year(int(m.group(3)))
    if not month or not (1 <= day <= 31) or not (1900 <= year <= 2100):
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_document_date_rich(value: str | None) -> ParsedDate:
    """Full date parsing returning quality metadata alongside the parsed date."""
    if not value:
        return ParsedDate(value=None)

    # Strip outer guillemets/quotes and whitespace
    text = value.strip().strip("«»\"“”' \t")

    # Detect trailing field bleed (e.g. "14 января 2025 г. №20") before stripping
    bleed = bool(_BLEED_RE.search(text))
    text = _BLEED_RE.sub("", text).strip()

    # 1. Date range — extract end date, mark is_range
    m_range = _RU_RANGE_RE.search(text)
    if m_range:
        end_date = _try_ru_date(m_range.group(2))
        if end_date:
            return ParsedDate(value=end_date, is_range=True, has_field_bleed=bleed)

    # 2. Russian text date: «DD» MMMM YYYY г. / DD MMMM YYYY года
    ru = _try_ru_date(text)
    if ru:
        return ParsedDate(value=ru, has_field_bleed=bleed)

    # 3. Numeric: DD.MM.YYYY / DD.MM.YY / YYYY.MM.DD
    num_clean = text.replace("/", ".").replace("-", ".").replace(" ", "")
    m_num = _NUMERIC_RE.match(num_clean)
    if m_num:
        a, b, c = int(m_num.group(1)), int(m_num.group(2)), int(m_num.group(3))
        try:
            if a > 31:  # YYYY.MM.DD
                return ParsedDate(value=date(_expand_year(a), b, c), has_field_bleed=bleed)
            else:       # DD.MM.YY[YY]
                return ParsedDate(value=date(_expand_year(c), b, a), has_field_bleed=bleed)
        except ValueError:
            pass

    # 4. Year-only → partial (only year known)
    m_year = _YEAR_ONLY_RE.match(text.strip())
    if m_year:
        year = int(m_year.group(1))
        if 1900 <= year <= 2100:
            return ParsedDate(value=date(year, 1, 1), is_partial=True, has_field_bleed=bleed)

    return ParsedDate(value=None, has_field_bleed=bleed)


def parse_document_date(value: str | None) -> date | None:
    """Backward-compatible wrapper: returns date or None (None for partial/unreadable)."""
    result = parse_document_date_rich(value)
    if result.value is None or result.is_partial:
        return None
    return result.value


# ── Rule evaluation ─────────────────────────────────────────────────────────────


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


def _any_name_matches(
    pages: Iterable[tuple[int, PageExtraction]], expected: str
) -> bool:
    """True if ANY name extracted across all pages matches the expected name."""
    expected_norm = normalize_fio(expected)
    if not expected_norm:
        return False
    for _, extraction in pages:
        if extraction.full_name.value and normalize_fio(extraction.full_name.value) == expected_norm:
            return True
    return False


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

    # fio_mismatch fires only when NO name across all pages matches the employee.
    # This prevents false positives from signatory/employer names in multi-page docs.
    if found_fio and expected_full_name and not _any_name_matches(pages, expected_full_name):
        issues.append(
            RuleIssue(
                issue_code="fio_mismatch",
                field_name="full_name",
                found_value=found_fio,
                confidence=fio_conf,
                page_number=page_fio,
                severity="high",
                message="ФИО в документе не совпадает с ФИО сотрудника (ни на одной странице)",
            )
        )

    expected_iin_norm = normalize_iin(expected_iin)
    found_iin_norm = normalize_iin(found_iin)
    iin_mismatch = bool(expected_iin_norm and found_iin_norm and expected_iin_norm != found_iin_norm)
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

    # other_employee_document: highest-confidence name AND iin both point to someone else.
    # Uses best-field fio (not all-pages) to identify the primary person in the document.
    best_fio_mismatches = bool(
        found_fio and expected_full_name
        and normalize_fio(found_fio) != normalize_fio(expected_full_name)
    )
    if best_fio_mismatches and iin_mismatch:
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
        parsed = parse_document_date_rich(expiry_date)
        if parsed.value is None:
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
        elif parsed.is_partial or parsed.is_range:
            # Partial (year-only) or range (end-date used) — operator must verify
            issues.append(
                RuleIssue(
                    issue_code="partial_expiry_date",
                    field_name="expiry_date",
                    found_value=expiry_date,
                    confidence=expiry_conf,
                    page_number=page_expiry,
                    severity="low",
                    message="Срок действия указан неполно или в виде диапазона — требует проверки",
                )
            )
        elif parsed.value < now:
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
