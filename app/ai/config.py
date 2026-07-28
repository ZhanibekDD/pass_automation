from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.config import BASE_DIR


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)


def _env_codes(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    return tuple(sorted({int(item.strip()) for item in value.split(",") if item.strip()}))


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if value is None:
        return default.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _env_str_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class AISettings:
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key: str
    model_api_key: str
    database_path: Path
    input_json_path: Path
    required_document_codes: tuple[int, ...]
    dated_document_codes: tuple[int, ...]
    confidence_threshold: float
    request_timeout_seconds: float
    max_file_size_mb: int
    max_pages: int
    render_dpi: int
    max_image_dimension: int
    # vehicle document codes (string-based, not int)
    required_vehicle_document_codes: tuple[str, ...]
    dated_vehicle_document_codes: tuple[str, ...]

    @classmethod
    def from_env(cls) -> AISettings:
        provider = os.getenv("AI_PROVIDER", "ollama").strip().lower()
        if provider not in {"ollama", "vllm"}:
            raise ValueError("AI_PROVIDER должен быть 'ollama' или 'vllm'")

        default_url = "http://ollama:11434" if provider == "ollama" else "http://host.docker.internal:8000"
        settings = cls(
            enabled=_env_bool("AI_ENABLED", False),
            provider=provider,
            base_url=os.getenv("AI_BASE_URL", default_url).strip().rstrip("/"),
            model=os.getenv("AI_VISION_MODEL", "qwen2.5vl:7b").strip(),
            api_key=os.getenv("AI_API_KEY", ""),
            model_api_key=os.getenv("AI_MODEL_API_KEY", ""),
            database_path=_env_path("AI_DATABASE_PATH", BASE_DIR / "data" / "ai" / "pass_docs_ai.sqlite3"),
            input_json_path=_env_path("AI_INPUT_JSON", BASE_DIR / "data" / "input" / "package_input.json"),
            required_document_codes=_env_codes("AI_REQUIRED_DOCUMENT_CODES", (6, 7, 44, 45, 52)),
            dated_document_codes=_env_codes(
                "AI_DATED_DOCUMENT_CODES",
                (6, 7, 13, 14, 19, 26, 37, 43, 44, 57, 61, 63, 64, 65, 66, 67),
            ),
            confidence_threshold=_env_float("AI_CONFIDENCE_THRESHOLD", 0.75),
            request_timeout_seconds=_env_float("AI_REQUEST_TIMEOUT_SECONDS", 120.0),
            max_file_size_mb=_env_int("AI_MAX_FILE_SIZE_MB", 20),
            max_pages=_env_int("AI_MAX_PAGES", 20),
            render_dpi=_env_int("AI_RENDER_DPI", 160),
            max_image_dimension=_env_int("AI_MAX_IMAGE_DIMENSION", 2200),
            required_vehicle_document_codes=_env_str_list(
                "AI_REQUIRED_VEHICLE_DOC_CODES",
                ("registration", "insurance", "inspection"),
            ),
            dated_vehicle_document_codes=_env_str_list(
                "AI_DATED_VEHICLE_DOC_CODES",
                ("insurance", "inspection", "power_of_attorney", "vehicle_pass"),
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("AI_BASE_URL должен быть HTTP(S) URL")
        if not self.model:
            raise ValueError("AI_VISION_MODEL не может быть пустым")
        if not 0 <= self.confidence_threshold <= 1:
            raise ValueError("AI_CONFIDENCE_THRESHOLD должен быть от 0 до 1")
        if self.max_file_size_mb <= 0 or self.max_pages <= 0:
            raise ValueError("AI_MAX_FILE_SIZE_MB и AI_MAX_PAGES должны быть больше нуля")
        if not 72 <= self.render_dpi <= 300:
            raise ValueError("AI_RENDER_DPI должен быть от 72 до 300")
        if self.max_image_dimension < 512:
            raise ValueError("AI_MAX_IMAGE_DIMENSION должен быть не меньше 512")
