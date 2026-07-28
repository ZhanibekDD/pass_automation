from pathlib import Path

import pytest

from app.services.validator import ValidationError, validate_pdf_file
from app.utils.fio import split_fio


def test_existing_fio_split_is_unchanged() -> None:
    assert split_fio("Иванов Иван Иванович") == (
        "Иванов",
        "Иван",
        "Иванович",
    )


def test_existing_validator_still_rejects_non_pdf(tmp_path: Path) -> None:
    source = tmp_path / "photo.png"
    source.write_bytes(b"not-a-pdf")
    with pytest.raises(ValidationError):
        validate_pdf_file(source)
