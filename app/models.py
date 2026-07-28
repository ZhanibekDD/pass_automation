from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PackageInput:
    fio: str
    employee_index: int
    documents: dict[int, Path]
    contractor_agreement: Path | None = None
    subcontract_agreement: Path | None = None
    signed_application_scan: Path | None = None
    iin: str | None = None
    # ISO YYYY-MM-DD, проверяется при загрузке JSON; в Excel — формат по шаблону (этап 4).
    birth_date: str | None = None
    company: str | None = None
    profession: str | None = None


@dataclass
class PackageResult:
    build_dir: Path
    zip_path: Path
    created_files: list[Path] = field(default_factory=list)
