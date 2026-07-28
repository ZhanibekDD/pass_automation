from __future__ import annotations

import tempfile
from pathlib import Path

from app.ai.config import AISettings
from app.ai.db import AIRepository

_DEFAULT_TMP: Path | None = None


def _tmp() -> Path:
    global _DEFAULT_TMP
    if _DEFAULT_TMP is None:
        _DEFAULT_TMP = Path(tempfile.mkdtemp())
    return _DEFAULT_TMP


def make_settings(
    tmp_path: Path | None = None,
    *,
    input_json_path: Path | None = None,
    enabled: bool = True,
    api_key: str = "",
    required_document_codes: tuple[int, ...] = (6, 7),
    dated_document_codes: tuple[int, ...] = (6, 7),
    max_file_size_mb: int = 5,
) -> AISettings:
    base = tmp_path or _tmp()
    return AISettings(
        enabled=enabled,
        provider="ollama",
        base_url="http://ollama.test:11434",
        model="vision-test",
        api_key=api_key,
        model_api_key="",
        database_path=base / "ai.sqlite3",
        input_json_path=input_json_path or base / "package_input.json",
        required_document_codes=required_document_codes,
        dated_document_codes=dated_document_codes,
        confidence_threshold=0.75,
        request_timeout_seconds=5,
        max_file_size_mb=max_file_size_mb,
        max_pages=5,
        render_dpi=120,
        max_image_dimension=1200,
        required_vehicle_document_codes=("registration", "insurance", "inspection"),
        dated_vehicle_document_codes=("insurance", "inspection"),
        pg_dsn="",
    )


def make_repository(settings: AISettings) -> AIRepository:
    repo = AIRepository(settings.database_path)
    repo.initialize()
    return repo
