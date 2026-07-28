import pytest

from app.ai.providers import ModelResponseError, parse_page_extraction

VALID_RESPONSE = {
    "document_type": {"value": "Удостоверение личности", "confidence": 0.9},
    "full_name": {"value": None, "confidence": 0},
    "iin": {"value": None, "confidence": 0},
    "issue_date": {"value": None, "confidence": 0},
    "expiry_date": {"value": None, "confidence": 0},
}


def test_parser_accepts_explicit_nulls() -> None:
    result = parse_page_extraction(VALID_RESPONSE)
    assert result.full_name.value is None
    assert result.full_name.confidence == 0


def test_parser_rejects_unexpected_hallucinated_fields() -> None:
    invalid = {**VALID_RESPONSE, "guessed_address": "Алматы"}
    with pytest.raises(ModelResponseError):
        parse_page_extraction(invalid)
