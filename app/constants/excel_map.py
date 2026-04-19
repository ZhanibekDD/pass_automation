"""
Предварительный маппинг ячеек листа «Реестр» шаблона АСДПО.

Только конфигурация: запись в Excel по этим адресам — отдельный шаг (после проверок A/B).
Строка данных: с 5-й по инструкции; для одного сотрудника в примере — строка 5.
"""

REESTR_SHEET_NAME = "Реестр"
REESTR_FIRST_DATA_ROW = 5

# Первая строка данных (один сотрудник). При нескольких — позже смещение строки.
REESTR_ROW = REESTR_FIRST_DATA_ROW

# Ключи совпадают с полями/смыслом payload и производными от ФИО.
REESTR_CELL_MAP: dict[str, str] = {
    "employee_index": f"A{REESTR_ROW}",
    "last_name": f"E{REESTR_ROW}",
    "first_name": f"F{REESTR_ROW}",
    "middle_name": f"G{REESTR_ROW}",
    "birth_date": f"H{REESTR_ROW}",
    "profession": f"T{REESTR_ROW}",
}
