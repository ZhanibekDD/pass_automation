"""Тесты правил и сервиса для транспортных средств."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ai.schemas import ExtractedField, VehicleDocumentSnapshot, VehiclePageExtraction, VehicleSnapshot
from app.ai.vehicles import (
    REQUIRED_VEHICLE_CODES,
    check_vehicle_completeness,
    evaluate_vehicle_document,
)


def _extraction(
    *,
    doc_type: str | None = "Страховой полис",
    driver_name: str | None = "Иванов Иван Иванович",
    plate_number: str | None = None,
    issue_date: str | None = "01.01.2024",
    expiry_date: str | None = None,
    confidence: float = 0.95,
) -> VehiclePageExtraction:
    return VehiclePageExtraction(
        document_type=ExtractedField(value=doc_type, confidence=confidence),
        plate_number=ExtractedField(value=plate_number, confidence=confidence),
        vin=ExtractedField(value=None, confidence=0.0),
        registration_number=ExtractedField(value=None, confidence=0.0),
        driver_name=ExtractedField(value=driver_name, confidence=confidence),
        issue_date=ExtractedField(value=issue_date, confidence=confidence),
        expiry_date=ExtractedField(value=expiry_date, confidence=confidence),
    )


def _vehicle(
    *,
    plate: str = "А123ВС77",
    driver: str = "Иванов Иван Иванович",
    docs: tuple = (),
) -> VehicleSnapshot:
    return VehicleSnapshot(
        vehicle_id="v1",
        plate_number=plate,
        make="КАМАЗ",
        model="65115",
        vehicle_type="грузовой",
        color="синий",
        owner="ООО Днепр",
        organization="ООО Днепр",
        driver=driver,
        site_object="Объект-1",
        pass_number="VP-001",
        pass_start="2024-01-01",
        pass_end="2025-01-01",
        status="active",
        access_zone="zone-a",
        documents=docs,
    )


def _doc(code: str = "insurance") -> VehicleDocumentSnapshot:
    return VehicleDocumentSnapshot(
        document_id=f"v1:{code}",
        document_code=code,
        source_path=Path("/dev/null"),
    )


# ------------------------------------------------------------------ completeness


def test_vehicle_completeness_all_missing() -> None:
    vehicle = _vehicle()
    issues = check_vehicle_completeness(vehicle)
    codes = {i.issue_code for i in issues}
    assert codes == {"missing_required_vehicle_doc"}
    assert len(issues) == len(REQUIRED_VEHICLE_CODES)


def test_vehicle_completeness_partial() -> None:
    vehicle = _vehicle(docs=(_doc("registration"),))
    issues = check_vehicle_completeness(vehicle)
    # registration present → только insurance, inspection отсутствуют
    assert len(issues) == 2


def test_vehicle_completeness_all_present() -> None:
    docs = tuple(_doc(c) for c in REQUIRED_VEHICLE_CODES)
    vehicle = _vehicle(docs=docs)
    issues = check_vehicle_completeness(vehicle)
    assert issues == []


def test_completeness_compares_document_code_not_id() -> None:
    """check_vehicle_completeness сравнивает document_code, а не document_id."""
    # document_id = "v1:insurance", document_code = "insurance"
    doc = _doc("insurance")
    assert doc.document_code == "insurance"
    assert doc.document_id == "v1:insurance"
    vehicle = _vehicle(docs=(doc,))
    issues = check_vehicle_completeness(vehicle)
    # Только registration и inspection отсутствуют, insurance засчитан по code
    assert "Страховой полис (ОСАГО/КАСКО)" not in [i.message for i in issues if "insurance" in i.message]
    assert len(issues) == 2  # registration + inspection


# ------------------------------------------------------------------ plate mismatch


def test_plate_mismatch_detected() -> None:
    pages = [(1, _extraction(plate_number="В456ГД77"))]
    vehicle = _vehicle(plate="А123ВС77")
    doc = _doc("registration")
    issues = evaluate_vehicle_document(vehicle=vehicle, document=doc, pages=pages, confidence_threshold=0.75)
    codes = [i.issue_code for i in issues]
    assert "vehicle_plate_mismatch" in codes


def test_plate_match_no_issue() -> None:
    pages = [(1, _extraction(plate_number="А123ВС77"))]
    vehicle = _vehicle(plate="А123ВС77")
    doc = _doc("registration")
    issues = evaluate_vehicle_document(vehicle=vehicle, document=doc, pages=pages, confidence_threshold=0.75)
    codes = [i.issue_code for i in issues]
    assert "vehicle_plate_mismatch" not in codes


# ------------------------------------------------------------------ driver mismatch


def test_driver_mismatch_detected() -> None:
    pages = [(1, _extraction(driver_name="Петров Пётр Петрович"))]
    vehicle = _vehicle(driver="Иванов Иван Иванович")
    doc = _doc("driver_license")
    issues = evaluate_vehicle_document(vehicle=vehicle, document=doc, pages=pages, confidence_threshold=0.75)
    codes = [i.issue_code for i in issues]
    assert "wrong_driver" in codes


def test_driver_field_name_is_driver_name() -> None:
    """wrong_driver использует поле driver_name, а не full_name или iin."""
    pages = [(1, _extraction(driver_name="Петров Пётр Петрович"))]
    vehicle = _vehicle(driver="Иванов Иван Иванович")
    doc = _doc("driver_license")
    issues = evaluate_vehicle_document(vehicle=vehicle, document=doc, pages=pages, confidence_threshold=0.75)
    wrong_driver_issues = [i for i in issues if i.issue_code == "wrong_driver"]
    assert wrong_driver_issues
    assert wrong_driver_issues[0].field_name == "driver_name"


# ------------------------------------------------------------------ expiry


def test_expired_doc_detected() -> None:
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).strftime("%d.%m.%Y")
    pages = [(1, _extraction(expiry_date=yesterday))]
    doc = _doc("insurance")
    issues = evaluate_vehicle_document(
        vehicle=_vehicle(), document=doc, pages=pages, confidence_threshold=0.75
    )
    codes = [i.issue_code for i in issues]
    assert "vehicle_doc_expired" in codes


def test_expiring_soon_detected() -> None:
    soon = (datetime.now(UTC).date() + timedelta(days=10)).strftime("%d.%m.%Y")
    pages = [(1, _extraction(expiry_date=soon))]
    doc = _doc("insurance")
    issues = evaluate_vehicle_document(
        vehicle=_vehicle(), document=doc, pages=pages, confidence_threshold=0.75
    )
    codes = [i.issue_code for i in issues]
    assert "vehicle_doc_expiring_soon" in codes


def test_valid_expiry_no_issue() -> None:
    future = (datetime.now(UTC).date() + timedelta(days=200)).strftime("%d.%m.%Y")
    pages = [(1, _extraction(expiry_date=future, plate_number=None, driver_name=None))]
    doc = _doc("insurance")
    issues = evaluate_vehicle_document(
        vehicle=_vehicle(driver=""),
        document=doc,
        pages=pages,
        confidence_threshold=0.75,
    )
    codes = [i.issue_code for i in issues]
    assert "vehicle_doc_expired" not in codes
    assert "vehicle_doc_expiring_soon" not in codes


def test_missing_expiry_for_dated_doc() -> None:
    pages = [(1, _extraction(expiry_date=None, plate_number=None, driver_name=None))]
    doc = _doc("insurance")
    issues = evaluate_vehicle_document(
        vehicle=_vehicle(driver=""),
        document=doc,
        pages=pages,
        confidence_threshold=0.75,
    )
    codes = [i.issue_code for i in issues]
    assert "vehicle_doc_missing_expiry" in codes


def test_no_expiry_check_for_non_dated_doc() -> None:
    pages = [(1, _extraction(expiry_date=None, plate_number=None, driver_name=None))]
    doc = _doc("vehicle_photo")
    issues = evaluate_vehicle_document(
        vehicle=_vehicle(driver=""),
        document=doc,
        pages=pages,
        confidence_threshold=0.75,
    )
    codes = [i.issue_code for i in issues]
    assert "vehicle_doc_missing_expiry" not in codes


# ------------------------------------------------------------------ low confidence


def test_low_confidence_flagged() -> None:
    pages = [(1, _extraction(plate_number="А123ВС77", confidence=0.5))]
    doc = _doc("registration")
    issues = evaluate_vehicle_document(
        vehicle=_vehicle(), document=doc, pages=pages, confidence_threshold=0.75
    )
    codes = [i.issue_code for i in issues]
    assert "low_confidence" in codes
