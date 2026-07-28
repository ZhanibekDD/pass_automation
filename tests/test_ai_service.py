from PIL import Image

from app.ai.db import AIRepository
from app.ai.providers import VisionProvider
from app.ai.schemas import (
    DocumentSnapshot,
    EmployeeSnapshot,
    ExtractedField,
    PageExtraction,
)
from app.ai.service import DocumentAIService
from tests.helpers import make_settings


class FakeVisionProvider(VisionProvider):
    def health(self) -> dict:
        return {"status": "ok", "provider": "fake", "model": "fake"}

    def extract(self, *, image_bytes: bytes, prompt: str) -> PageExtraction:
        return PageExtraction(
            document_type=ExtractedField(value="Удостоверение", confidence=0.96),
            full_name=ExtractedField(value="Другой Сотрудник Тестович", confidence=0.94),
            iin=ExtractedField(value="1111", confidence=0.93),
            issue_date=ExtractedField(value="01.01.2020", confidence=0.9),
            expiry_date=ExtractedField(value="01.01.2025", confidence=0.92),
        )


def test_service_persists_results_and_review_queue(tmp_path) -> None:
    image_path = tmp_path / "identity.png"
    Image.new("RGB", (800, 600), "white").save(image_path)
    settings = make_settings(tmp_path)
    repository = AIRepository(settings.database_path)
    repository.initialize()
    service = DocumentAIService(
        settings=settings,
        repository=repository,
        provider=FakeVisionProvider(settings),
    )
    employee = EmployeeSnapshot(
        employee_id="42",
        full_name="Иванов Иван Иванович",
        iin="2222",
        documents=(
            DocumentSnapshot(
                document_id="42:6",
                document_code=6,
                source_path=image_path,
            ),
        ),
    )

    result = service.analyze_document(employee, employee.documents[0])
    assert len(result["extractions"]) == 5
    codes = {item["issue_code"] for item in result["findings"]}
    assert "other_employee_document" in codes
    assert "expired_document" in codes
    assert repository.summary()["review_queue"]["pending"] >= 2
