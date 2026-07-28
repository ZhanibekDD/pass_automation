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
    """Строгий контракт ответа vision-модели для одной страницы."""

    model_config = ConfigDict(extra="forbid")

    document_type: ExtractedField
    full_name: ExtractedField
    iin: ExtractedField
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


class ReviewUpdate(BaseModel):
    status: Literal["confirmed", "rejected"]
    comment: str = Field(default="", max_length=2000)
