import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import BASE_DIR
from app.models import PackageInput


class InputLoaderError(Exception):
    pass


def _require_field(data: dict[str, Any], field_name: str) -> Any:
    if field_name not in data:
        raise InputLoaderError(f"Отсутствует обязательное поле: {field_name}")
    return data[field_name]


def _resolve_path(value: str) -> Path:
    p = Path(value.strip())
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (BASE_DIR / p).resolve()

    if not resolved.exists():
        raise InputLoaderError(f"Файл не найден: {resolved}")

    return resolved


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    if key not in data or data[key] is None:
        return None
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise InputLoaderError(
            f"Поле {key!r} должно быть непустой строкой или отсутствовать / null"
        )
    return value.strip()


def _optional_date(data: dict[str, Any], key: str) -> str | None:
    if key not in data or data[key] is None:
        return None

    value = data[key]

    if not isinstance(value, str) or not value.strip():
        raise InputLoaderError(
            f"Поле {key!r} должно быть строкой в формате YYYY-MM-DD или отсутствовать / null"
        )

    s = value.strip()
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise InputLoaderError(
            f"Неверный формат даты для {key!r}. Ожидается YYYY-MM-DD"
        ) from e

    return s


def _to_path(value: Any) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InputLoaderError("Опциональный путь к файлу должен быть непустой строкой или null")
    return _resolve_path(value)


def load_package_input(json_path: Path) -> PackageInput:
    if not json_path.exists():
        raise InputLoaderError(f"JSON файл не найден: {json_path}")

    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise InputLoaderError(f"Ошибка чтения JSON: {e}") from e

    if not isinstance(raw, dict):
        raise InputLoaderError("Корень JSON должен быть объектом")

    fio = _require_field(raw, "fio")
    employee_index = _require_field(raw, "employee_index")
    documents_raw = _require_field(raw, "documents")

    if not isinstance(fio, str) or not fio.strip():
        raise InputLoaderError("Поле fio должно быть непустой строкой")

    if isinstance(employee_index, bool) or not isinstance(employee_index, int):
        raise InputLoaderError("Поле employee_index должно быть целым числом (int)")

    if not isinstance(documents_raw, dict):
        raise InputLoaderError("Поле documents должно быть объектом")

    documents: dict[int, Path] = {}

    for doc_code_str, file_path_str in documents_raw.items():
        try:
            doc_code = int(doc_code_str)
        except (TypeError, ValueError) as e:
            raise InputLoaderError(
                f"Ключ документа должен быть числом, получено: {doc_code_str!r}"
            ) from e

        if not isinstance(file_path_str, str) or not file_path_str.strip():
            raise InputLoaderError(
                f"Путь для документа {doc_code} должен быть непустой строкой"
            )

        documents[doc_code] = _resolve_path(file_path_str)

    if not documents:
        raise InputLoaderError("Список документов пуст")

    return PackageInput(
        fio=fio.strip(),
        employee_index=employee_index,
        documents=documents,
        contractor_agreement=_to_path(raw.get("contractor_agreement")),
        subcontract_agreement=_to_path(raw.get("subcontract_agreement")),
        signed_application_scan=_to_path(raw.get("signed_application_scan")),
        iin=_optional_str(raw, "iin"),
        birth_date=_optional_date(raw, "birth_date"),
        company=_optional_str(raw, "company"),
        profession=_optional_str(raw, "profession"),
    )
