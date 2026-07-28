from __future__ import annotations

from pathlib import Path

from app.ai.config import AISettings


def make_settings(
    tmp_path: Path,
    *,
    input_json_path: Path | None = None,
    enabled: bool = True,
    api_key: str = "",
    required_document_codes: tuple[int, ...] = (6, 7),
    dated_document_codes: tuple[int, ...] = (6, 7),
) -> AISettings:
    return AISettings(
        enabled=enabled,
        provider="ollama",
        base_url="http://ollama.test:11434",
        model="vision-test",
        api_key=api_key,
        model_api_key="",
        database_path=tmp_path / "ai.sqlite3",
        input_json_path=input_json_path or tmp_path / "package_input.json",
        required_document_codes=required_document_codes,
        dated_document_codes=dated_document_codes,
        confidence_threshold=0.75,
        request_timeout_seconds=5,
        max_file_size_mb=5,
        max_pages=5,
        render_dpi=120,
        max_image_dimension=1200,
    )
