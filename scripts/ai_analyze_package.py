"""Локальный запуск анализа текущего package_input.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.ai.adapters import load_package_snapshot  # noqa: E402
from app.ai.config import AISettings  # noqa: E402
from app.ai.db import AIRepository  # noqa: E402
from app.ai.providers import create_provider  # noqa: E402
from app.ai.service import DocumentAIService  # noqa: E402


def main() -> None:
    settings = AISettings.from_env()
    if not settings.enabled:
        raise SystemExit("AI отключён. Установите AI_ENABLED=true")

    repository = AIRepository(settings.database_path)
    repository.initialize()
    provider = create_provider(settings)
    employee = load_package_snapshot(settings.input_json_path)
    service = DocumentAIService(
        settings=settings,
        repository=repository,
        provider=provider,
    )
    result = service.analyze_employee(employee)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["failed_documents"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
