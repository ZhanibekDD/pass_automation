"""
Запись строки «Реестр» в копию боевого шаблона через установленный Microsoft Excel (xlwings).

Не использует openpyxl для сохранения книги — снижает риск потери Data Validation и
Conditional Formatting. На машине должен быть установлен Excel.

Не подключён к package_builder / ExcelGenerator — отдельный backend на этап внедрения.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import xlwings as xw

from app.constants.excel_map import REESTR_CELL_MAP, REESTR_SHEET_NAME
from app.models import PackageInput
from app.services.reestr_payload_builder import build_reestr_row_data


def export_workbook_with_reestr_xlwings(
    template_path: Path,
    output_path: Path,
    payload: PackageInput,
    *,
    visible: bool = False,
) -> Path:
    """
    Копирует шаблон в output_path, открывает копию в Excel, заполняет лист «Реестр»
    по REESTR_CELL_MAP и build_reestr_row_data(payload), сохраняет и закрывает книгу.

    Поля: employee_index, last_name, first_name, middle_name, birth_date (ISO → datetime
    в ячейку при непустом значении), profession.
    """
    if not template_path.exists():
        raise FileNotFoundError(f"Шаблон не найден: {template_path}")

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, output_path)

    row_data = build_reestr_row_data(payload)

    app = None
    wb = None
    try:
        try:
            app = xw.App(visible=visible, add_book=False)
        except Exception as e:
            raise RuntimeError(
                "Не удалось запустить Microsoft Excel через xlwings. "
                "Проверьте, что Excel установлен и доступен для автоматизации."
            ) from e

        app.display_alerts = False
        app.screen_updating = False

        wb = app.books.open(str(output_path), update_links=False, read_only=False)
        sheet_names = [s.name for s in wb.sheets]
        if REESTR_SHEET_NAME not in sheet_names:
            raise ValueError(
                f"В книге нет листа {REESTR_SHEET_NAME!r}. Листы: {sheet_names!r}"
            )
        sheet = wb.sheets[REESTR_SHEET_NAME]

        for key, cell in REESTR_CELL_MAP.items():
            if not cell:
                continue
            raw = row_data.get(key, "")
            val: object
            if key == "birth_date" and raw != "" and isinstance(raw, str):
                try:
                    val = datetime.strptime(raw, "%Y-%m-%d")
                except ValueError:
                    val = raw
            else:
                val = raw

            sheet.range(cell).value = val

        wb.save()
    finally:
        if wb is not None:
            wb.close()
        if app is not None:
            app.quit()

    return output_path
