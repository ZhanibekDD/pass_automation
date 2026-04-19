from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass
class PackageInput:
    fio: str
    employee_index: int
    documents: Dict[int, Path]
    contractor_agreement: Optional[Path] = None
    subcontract_agreement: Optional[Path] = None
    signed_application_scan: Optional[Path] = None
    iin: Optional[str] = None
    # ISO YYYY-MM-DD, проверяется при загрузке JSON; в Excel — формат по шаблону (этап 4).
    birth_date: Optional[str] = None
    company: Optional[str] = None
    profession: Optional[str] = None


@dataclass
class PackageResult:
    build_dir: Path
    zip_path: Path
    created_files: list[Path] = field(default_factory=list)
