"""Сборка данных одной строки листа «Реестр» из PackageInput (без записи в Excel)."""

from app.models import PackageInput
from app.utils.fio import split_fio


def build_reestr_row_data(payload: PackageInput) -> dict[str, str | int]:
    """
    Данные одной строки листа «Реестр» под ключи из REESTR_CELL_MAP.

    Формат: employee_index (int); last_name, first_name, middle_name, birth_date (YYYY-MM-DD
    из JSON или ""), profession (строка или "").
    """
    last_name, first_name, middle_name = split_fio(payload.fio)
    birth = payload.birth_date or ""
    prof = payload.profession or ""

    return {
        "employee_index": payload.employee_index,
        "last_name": last_name,
        "first_name": first_name,
        "middle_name": middle_name,
        "birth_date": birth,
        "profession": prof,
    }
