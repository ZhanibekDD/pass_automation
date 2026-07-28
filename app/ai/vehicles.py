"""Правила проверки транспортных документов."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.ai.rules import parse_document_date
from app.ai.schemas import (
    RuleIssue,
    VehicleDocumentSnapshot,
    VehiclePageExtraction,
    VehicleSnapshot,
)

VEHICLE_DOCUMENT_CODES: dict[str, str] = {
    "registration": "Свидетельство о регистрации ТС",
    "insurance": "Страховой полис (ОСАГО/КАСКО)",
    "inspection": "Техосмотр / талон",
    "power_of_attorney": "Доверенность на управление",
    "driver_license": "Водительское удостоверение",
    "vehicle_pass": "Пропуск транспортного средства",
    "vehicle_photo": "Фотография ТС",
    "special_permit": "Специальное разрешение",
}

REQUIRED_VEHICLE_CODES = frozenset({"registration", "insurance", "inspection"})
DATED_VEHICLE_CODES = frozenset({"insurance", "inspection", "power_of_attorney", "vehicle_pass"})
EXPIRY_SOON_DAYS = 30


def evaluate_vehicle_document(
    *,
    vehicle: VehicleSnapshot,
    document: VehicleDocumentSnapshot,
    pages: list[tuple[int, VehiclePageExtraction]],
    confidence_threshold: float,
    today: date | None = None,
) -> list[RuleIssue]:
    """Проверяет один документ транспортного средства."""
    now = today or datetime.now(UTC).date()
    issues: list[RuleIssue] = []

    def best(field_name: str) -> tuple[int | None, str | None, float]:
        candidates = []
        for page_number, extraction in pages:
            field = getattr(extraction, field_name)
            if field.value is not None:
                candidates.append((field.confidence, page_number, field.value))
        if not candidates:
            return None, None, 0.0
        conf, page, val = max(candidates)
        return page, val, conf

    _, found_plate, plate_conf = best("plate_number")
    _, found_driver, driver_conf = best("driver_name")
    page_expiry, expiry_str, expiry_conf = best("expiry_date")

    # 1. Несовпадение госномера
    if vehicle.plate_number and found_plate:
        norm_plate = _norm_plate(vehicle.plate_number)
        norm_found = _norm_plate(found_plate)
        if norm_plate and norm_found and norm_plate != norm_found:
            issues.append(
                RuleIssue(
                    issue_code="vehicle_plate_mismatch",
                    field_name="plate_number",
                    found_value=found_plate,
                    confidence=plate_conf,
                    page_number=None,
                    severity="high",
                    message=(
                        f"Госномер в документе «{found_plate}» не совпадает с ТС «{vehicle.plate_number}»"
                    ),
                )
            )

    # 2. Неверный водитель
    if vehicle.driver and found_driver:
        from app.ai.rules import normalize_fio

        if normalize_fio(found_driver) != normalize_fio(vehicle.driver):
            issues.append(
                RuleIssue(
                    issue_code="wrong_driver",
                    field_name="driver_name",
                    found_value=found_driver,
                    confidence=driver_conf,
                    page_number=None,
                    severity="high",
                    message=(f"ФИО в документе «{found_driver}» не совпадает с водителем «{vehicle.driver}»"),
                )
            )

    # 3. Проверка сроков
    if document.document_code in DATED_VEHICLE_CODES:
        if not expiry_str:
            issues.append(
                RuleIssue(
                    issue_code="vehicle_doc_missing_expiry",
                    field_name="expiry_date",
                    found_value=None,
                    confidence=0.0,
                    page_number=None,
                    severity="medium",
                    message=f"Срок действия документа «{document.document_code}» не найден",
                )
            )
        else:
            parsed = parse_document_date(expiry_str)
            if parsed is None:
                issues.append(
                    RuleIssue(
                        issue_code="unreadable_expiry_date",
                        field_name="expiry_date",
                        found_value=expiry_str,
                        confidence=expiry_conf,
                        page_number=page_expiry,
                        severity="medium",
                        message="Дата истечения не распознана",
                    )
                )
            elif parsed < now:
                issues.append(
                    RuleIssue(
                        issue_code="vehicle_doc_expired",
                        field_name="expiry_date",
                        found_value=expiry_str,
                        confidence=expiry_conf,
                        page_number=page_expiry,
                        severity="high",
                        message=f"Документ «{document.document_code}» просрочен ({parsed})",
                    )
                )
            elif (parsed - now).days <= EXPIRY_SOON_DAYS:
                issues.append(
                    RuleIssue(
                        issue_code="vehicle_doc_expiring_soon",
                        field_name="expiry_date",
                        found_value=expiry_str,
                        confidence=expiry_conf,
                        page_number=page_expiry,
                        severity="medium",
                        message=(
                            f"Документ «{document.document_code}» истекает "
                            f"через {(parsed - now).days} дн. ({parsed})"
                        ),
                    )
                )

    # 4. Низкая уверенность
    for field_name in ("driver_name", "plate_number", "vin", "expiry_date", "issue_date"):
        page_n, value, conf = best(field_name)
        if value is not None and conf < confidence_threshold:
            issues.append(
                RuleIssue(
                    issue_code="low_confidence",
                    field_name=field_name,
                    found_value=value,
                    confidence=conf,
                    page_number=page_n,
                    severity="medium",
                    message=f"Поле {field_name} требует ручной проверки (conf={conf:.2f})",
                )
            )

    return issues


def check_vehicle_completeness(
    vehicle: VehicleSnapshot,
    *,
    required_codes: frozenset[str] | None = None,
) -> list[RuleIssue]:
    """Возвращает issues для отсутствующих обязательных документов.

    Сравниваем по document_code (строка «registration», «insurance»…),
    а не по document_id (составной «v1:insurance»).
    """
    codes = required_codes if required_codes is not None else REQUIRED_VEHICLE_CODES
    existing = {doc.document_code for doc in vehicle.documents}
    issues = []
    for code in sorted(codes - existing):
        issues.append(
            RuleIssue(
                issue_code="missing_required_vehicle_doc",
                field_name="document_code",
                found_value=None,
                confidence=1.0,
                page_number=None,
                severity="high",
                message=f"Отсутствует обязательный документ ТС: {VEHICLE_DOCUMENT_CODES.get(code, code)}",
            )
        )
    return issues


def _norm_plate(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())
