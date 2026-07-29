from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtractedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | None
    confidence: float = Field(ge=0, le=1)

    @field_validator("value")
    @classmethod
    def clean_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split()).strip()
        return cleaned or None


class PageExtraction(BaseModel):
    """Строгий контракт ответа vision-модели для страницы документа сотрудника."""

    model_config = ConfigDict(extra="forbid")

    document_type: ExtractedField
    full_name: ExtractedField
    iin: ExtractedField
    issue_date: ExtractedField
    expiry_date: ExtractedField


class VehiclePageExtraction(BaseModel):
    """Строгий контракт ответа vision-модели для страницы документа ТС.

    Использует именованные поля ТС — не переиспользует iin/full_name.
    """

    model_config = ConfigDict(extra="forbid")

    document_type: ExtractedField
    plate_number: ExtractedField       # Госномер ТС (напр. «А123ВС77»)
    vin: ExtractedField                # VIN или номер кузова
    registration_number: ExtractedField  # Номер свидетельства / полиса / талона
    driver_name: ExtractedField        # ФИО водителя или доверенного лица
    issue_date: ExtractedField
    expiry_date: ExtractedField


@dataclass(frozen=True)
class PreparedPage:
    number: int
    image_bytes: bytes
    media_type: str
    width: int
    height: int


@dataclass(frozen=True)
class DocumentSnapshot:
    document_id: str
    document_code: int
    source_path: Path


@dataclass(frozen=True)
class EmployeeSnapshot:
    employee_id: str
    full_name: str
    iin: str | None
    documents: tuple[DocumentSnapshot, ...]


@dataclass(frozen=True)
class VehicleDocumentSnapshot:
    document_id: str
    document_code: str  # 'registration', 'insurance', 'inspection', etc.
    source_path: Path


@dataclass(frozen=True)
class VehicleSnapshot:
    vehicle_id: str
    plate_number: str
    make: str
    model: str
    vehicle_type: str
    color: str
    owner: str
    organization: str
    driver: str
    site_object: str
    pass_number: str
    pass_start: str | None
    pass_end: str | None
    status: str
    access_zone: str
    documents: tuple[VehicleDocumentSnapshot, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RuleIssue:
    issue_code: str
    field_name: str | None
    found_value: str | None
    confidence: float
    page_number: int | None
    severity: Literal["low", "medium", "high"]
    message: str


OperatorReason = Literal[
    "confirmed_raw",    # value is correct as extracted
    "wrong_role",       # name/value belongs to signatory or employer, not the subject
    "incomplete_value", # partial value (year-only date, truncated name)
    "field_bleed",      # adjacent field content leaked into this field
    "incorrect_value",  # OCR error or model hallucination
]


class ReviewUpdate(BaseModel):
    status: Literal["confirmed", "rejected"]
    reason: OperatorReason | None = None
    comment: str = Field(default="", max_length=2000)


class VehicleAnalyzeRequest(BaseModel):
    """Тело запроса POST /api/ai/vehicles/{vehicle_id}/analyze."""

    model_config = ConfigDict(extra="forbid")

    # Данные ТС (поля проверяются против документа)
    plate_number: str = Field(default="", max_length=20)
    make: str = Field(default="", max_length=100)
    vehicle_model: str = Field(default="", max_length=100)
    vehicle_type: str = Field(default="", max_length=50)
    color: str = Field(default="", max_length=50)
    owner: str = Field(default="", max_length=256)
    organization: str = Field(default="", max_length=256)
    driver: str = Field(default="", max_length=256)
    site_object: str = Field(default="", max_length=256)
    pass_number: str = Field(default="", max_length=50)
    # Документ ТС
    document_code: str = Field(min_length=1, max_length=64)
    document_id: str = Field(min_length=1, max_length=256)
    # Путь к файлу на сервере (должен быть внутри data/input/)
    source_path: str = Field(min_length=1, max_length=4096)
