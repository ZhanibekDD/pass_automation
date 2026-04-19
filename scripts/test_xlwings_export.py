"""
Разовый тест xlwings: копия шаблона + запись строки «Реестр».

Запуск из корня проекта pass_automation:
  python scripts/test_xlwings_export.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config import BASE_DIR
from app.services.input_loader import load_package_input
from app.services.excel_writer_xlwings import export_workbook_with_reestr_xlwings


def main() -> None:
    payload = load_package_input(
        (BASE_DIR / "data" / "input" / "package_input.json").resolve()
    )

    template_path = (BASE_DIR / "data" / "templates" / "asdpo_template.xlsx").resolve()
    output_path = (BASE_DIR / "data" / "output" / "xlwings_test.xlsx").resolve()

    result = export_workbook_with_reestr_xlwings(
        template_path=template_path,
        output_path=output_path,
        payload=payload,
        visible=True,
    )

    print("Готово:", result)


if __name__ == "__main__":
    main()
