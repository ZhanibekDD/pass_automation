"""Генерация файла заявки Excel по шаблону АСДПО (xlwings — сохранение валидаций и форматирования)."""

from pathlib import Path

from app.models import PackageInput
from app.services.excel_writer_xlwings import export_workbook_with_reestr_xlwings


class ExcelGenerator:
    def __init__(self, template_path: Path):
        self.template_path = template_path

    def generate(self, output_path: Path, payload: PackageInput) -> Path:
        return export_workbook_with_reestr_xlwings(
            template_path=self.template_path,
            output_path=output_path,
            payload=payload,
            visible=False,
        )
