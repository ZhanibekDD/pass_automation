import json

from fastapi.testclient import TestClient

from app.ai.api import create_app
from app.ai.db import AIRepository
from app.ai.providers import DisabledVisionProvider
from tests.helpers import make_settings


def test_required_endpoints_and_manual_review(tmp_path) -> None:
    document = tmp_path / "passport.pdf"
    document.write_bytes(b"%PDF-1.4\n%%EOF\n")
    input_json = tmp_path / "package_input.json"
    input_json.write_text(
        json.dumps(
            {
                "fio": "Тестов Тест Тестович",
                "employee_index": 42,
                "iin": "1111",
                "documents": {"6": str(document)},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings = make_settings(
        tmp_path, input_json_path=input_json, enabled=False, api_key="local-secret"
    )
    repository = AIRepository(settings.database_path)
    repository.initialize()
    finding_id = repository.add_finding(
        run_id=None,
        employee_id="42",
        document_id="42:6",
        issue_code="empty_dates",
        field_name="issue_date,expiry_date",
        found_value=None,
        confidence=0,
        source_file=str(document),
        page_number=1,
        severity="medium",
        message="Даты не найдены",
    )
    queue_id = repository.enqueue_finding(
        finding_id=finding_id,
        employee_id="42",
        document_id="42:6",
        reason_code="empty_dates",
        priority="medium",
    )
    app = create_app(
        settings=settings,
        repository=repository,
        provider=DisabledVisionProvider(settings),
    )
    client = TestClient(app)
    headers = {"X-API-Key": "local-secret", "X-Operator-ID": "operator-1"}

    assert client.get("/api/ai/health").status_code == 200
    assert client.get("/api/ai/summary").status_code == 401
    assert client.get("/api/ai/summary", headers=headers).status_code == 200

    completeness = client.get("/api/ai/employees/42/completeness", headers=headers)
    assert completeness.status_code == 200
    assert completeness.json()["missing_documents"][0]["document_code"] == 7

    queue = client.get("/api/ai/review-queue", headers=headers)
    assert queue.status_code == 200
    assert queue.json()["items"][0]["id"] == queue_id

    analysis = client.get("/api/ai/documents/42:6/analysis", headers=headers)
    assert analysis.status_code == 200
    assert analysis.json()["findings"][0]["found_value"] is None

    reviewed = client.patch(
        f"/api/ai/review-queue/{queue_id}",
        headers=headers,
        json={"status": "confirmed", "comment": "Проверено"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "confirmed"
