"""Запись подготовленных данных в лист Excel (отдельно от пайплайна сборки пакета)."""

from openpyxl.worksheet.worksheet import Worksheet


def write_reestr_row(
    ws: Worksheet,
    row_data: dict[str, str | int],
    cell_map: dict[str, str],
) -> None:
    """
    Записывает данные одной строки листа «Реестр» в worksheet по карте ячеек.

    Для каждой пары (ключ, адрес) из cell_map: в ws[адрес] пишется row_data[key];
    если ключа нет в row_data — в ячейку пишется пустая строка (не None).
    Пустой адрес ячейки в карте пропускается.
    """
    for key, cell in cell_map.items():
        if not cell:
            continue
        ws[cell] = row_data.get(key, "")
