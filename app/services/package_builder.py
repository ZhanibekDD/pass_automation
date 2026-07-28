from pathlib import Path

from slugify import slugify

from app.config import (
    BASE_DIR,
    CONTRACTOR_FOLDER_NAME,
    OUTPUT_DIR,
    PDF_FOLDER_NAME,
    SUBCONTRACTOR_FOLDER_NAME,
)
from app.constants.doc_catalog import DOC_CATALOG
from app.models import PackageInput, PackageResult
from app.services.excel_generator import ExcelGenerator
from app.services.file_ops import copy_file, ensure_dir, safe_name
from app.services.validator import validate_common_docs, validate_documents_map
from app.services.zip_service import create_zip_from_dir


class PackageBuilder:
    """Сборка пакета АСДПО (персонал): Excel по шаблону, затем PDF и ZIP."""

    def build(self, payload: PackageInput) -> PackageResult:
        validate_documents_map(payload.documents)
        validate_common_docs(
            payload.contractor_agreement,
            payload.subcontract_agreement,
            payload.signed_application_scan,
        )

        fio_safe = safe_name(payload.fio)
        slug = slugify(
            f"{payload.employee_index}_{fio_safe}",
            lowercase=False,
            separator="_",
        )
        build_dir = ensure_dir(OUTPUT_DIR / slug)

        pdf_root = ensure_dir(build_dir / PDF_FOLDER_NAME)
        employee_folder = ensure_dir(pdf_root / f"{payload.employee_index}&{fio_safe}")
        contractor_folder = ensure_dir(pdf_root / CONTRACTOR_FOLDER_NAME)
        subcontractor_folder = ensure_dir(pdf_root / SUBCONTRACTOR_FOLDER_NAME)

        created_files: list[Path] = []

        for doc_code, src_path in payload.documents.items():
            human_name = safe_name(DOC_CATALOG[doc_code])
            dst_name = f"{doc_code}&{human_name}.pdf"
            dst_path = employee_folder / dst_name
            created_files.append(copy_file(src_path, dst_path))

        if payload.contractor_agreement:
            dst = contractor_folder / f"1&{safe_name(DOC_CATALOG[1])}.pdf"
            created_files.append(copy_file(payload.contractor_agreement, dst))

        if payload.subcontract_agreement:
            dst = subcontractor_folder / f"2&{safe_name(DOC_CATALOG[2])}.pdf"
            created_files.append(copy_file(payload.subcontract_agreement, dst))

        if payload.signed_application_scan:
            dst = contractor_folder / f"33&{safe_name(DOC_CATALOG[33])}.pdf"
            created_files.append(copy_file(payload.signed_application_scan, dst))

        template_path = BASE_DIR / "data" / "templates" / "asdpo_template.xlsx"
        excel_path = build_dir / "Заявка.xlsx"
        ExcelGenerator(template_path).generate(output_path=excel_path, payload=payload)
        created_files.append(excel_path)

        zip_path = build_dir.with_suffix(".zip")
        create_zip_from_dir(build_dir, zip_path)

        return PackageResult(
            build_dir=build_dir,
            zip_path=zip_path,
            created_files=created_files,
        )
