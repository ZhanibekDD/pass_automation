"""Тесты адаптеров источников данных (JSON, DAS) и employee analyze endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.ai.adapters import DASHttpAdapter, load_package_snapshot

# ── helpers ────────────────────────────────────────────────────────────────────

def _mock_http_client(status_code: int, body, content_type: str = "application/json"):
    """Возвращает мок httpx.Client, подходящий для контекст-менеджера."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.headers = {"content-type": content_type}
    if isinstance(body, bytes):
        mock_response.content = body
        # Не парсим произвольные байты как JSON — бинарные ответы (.pdf, .png) не JSON
    else:
        mock_response.json.return_value = body
        mock_response.content = json.dumps(body).encode()
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    return mock_client


_EMPLOYEE_RESPONSE = {
    "employee_id": "123",
    "import_key": "IMP-123",
    "full_name": "Петров Пётр Петрович",
    "iin": "987654321098",
    "company": "ООО Тест",
    "profession_label": "Инженер",
    "is_active": True,
    "documents": [
        {
            "document_id": "123:6",
            "document_code": 6,
            "document_type_name": "Удостоверение личности",
            "source_path": "/server/path/id.pdf",
            "parse_status": "parsed",
            "status": "active",
        }
    ],
}


# ── JSON adapter ───────────────────────────────────────────────────────────────

def test_json_adapter_loads_snapshot(tmp_path: Path) -> None:
    """load_package_snapshot читает package_input.json и возвращает EmployeeSnapshot."""
    doc_file = tmp_path / "id.pdf"
    doc_file.write_bytes(b"%PDF-1.4\n%%EOF")

    input_file = tmp_path / "package_input.json"
    input_file.write_text(
        json.dumps({
            "employee_index": 42,
            "fio": "Иванов Иван Иванович",
            "iin": "123456789012",
            "documents": {6: str(doc_file)},
        }),
        encoding="utf-8",
    )

    snapshot = load_package_snapshot(input_file)
    assert snapshot.employee_id == "42"
    assert snapshot.full_name == "Иванов Иван Иванович"
    assert snapshot.iin == "123456789012"
    assert len(snapshot.documents) == 1
    assert snapshot.documents[0].document_id == "42:6"


def test_json_adapter_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises((FileNotFoundError, Exception)):
        load_package_snapshot(tmp_path / "nonexistent.json")


# ── DAS adapter: load_employee ────────────────────────────────────────────────

def test_das_adapter_loads_employee() -> None:
    """DASHttpAdapter.load_employee() корректно парсит ответ DAS API."""
    adapter = DASHttpAdapter(base_url="http://das.internal:8000", token="test-tok")
    mock_client = _mock_http_client(200, _EMPLOYEE_RESPONSE)
    with patch("httpx.Client", return_value=mock_client):
        snapshot = adapter.load_employee(123)

    assert snapshot.employee_id == "123"
    assert snapshot.full_name == "Петров Пётр Петрович"
    assert snapshot.iin == "987654321098"
    assert len(snapshot.documents) == 1
    assert snapshot.documents[0].document_id == "123:6"
    assert snapshot.documents[0].document_code == 6


def test_das_adapter_load_employee_401_raises_permission_error() -> None:
    adapter = DASHttpAdapter(base_url="http://das.internal:8000", token="bad-tok")
    mock_client = _mock_http_client(401, {"error": "Unauthorized"})
    with patch("httpx.Client", return_value=mock_client):
        with pytest.raises(PermissionError, match="401"):
            adapter.load_employee(123)


def test_das_adapter_load_employee_404_raises_file_not_found() -> None:
    adapter = DASHttpAdapter(base_url="http://das.internal:8000", token="test-tok")
    mock_client = _mock_http_client(404, {"error": "Employee not found"})
    with patch("httpx.Client", return_value=mock_client):
        with pytest.raises(FileNotFoundError, match="123"):
            adapter.load_employee(123)


def test_das_adapter_list_employees() -> None:
    adapter = DASHttpAdapter(base_url="http://das.internal:8000", token="test-tok")
    list_data = {"total": 2, "offset": 0, "limit": 100, "employees": [{"id": 1}, {"id": 2}]}
    mock_client = _mock_http_client(200, list_data)
    with patch("httpx.Client", return_value=mock_client):
        ids = adapter.list_employee_ids()
    assert ids == [1, 2]


def test_das_adapter_list_employees_401_raises() -> None:
    adapter = DASHttpAdapter(base_url="http://das.internal:8000", token="bad")
    mock_client = _mock_http_client(401, {"error": "Unauthorized"})
    with patch("httpx.Client", return_value=mock_client):
        with pytest.raises(PermissionError):
            adapter.list_employee_ids()


# ── DAS adapter: download_document_file ───────────────────────────────────────

def test_das_adapter_download_document_pdf(tmp_path: Path) -> None:
    """download_document_file сохраняет PDF в dest_dir с расширением .pdf."""
    adapter = DASHttpAdapter(base_url="http://das.internal:8000", token="test-tok")
    pdf_bytes = b"%PDF-1.4\n%%EOF"
    mock_client = _mock_http_client(200, pdf_bytes, content_type="application/pdf")
    mock_client.get.return_value.content = pdf_bytes

    with patch("httpx.Client", return_value=mock_client):
        dest = adapter.download_document_file("123:6", dest_dir=tmp_path)

    assert dest.exists()
    assert dest.suffix == ".pdf"
    assert dest.read_bytes() == pdf_bytes


def test_das_adapter_download_document_png(tmp_path: Path) -> None:
    """download_document_file с PNG Content-Type сохраняет .png."""
    adapter = DASHttpAdapter(base_url="http://das.internal:8000", token="test-tok")
    png_bytes = b"\x89PNG\r\n\x1a\n"
    mock_client = _mock_http_client(200, png_bytes, content_type="image/png")
    mock_client.get.return_value.content = png_bytes

    with patch("httpx.Client", return_value=mock_client):
        dest = adapter.download_document_file("123:6", dest_dir=tmp_path)

    assert dest.suffix == ".png"


def test_das_adapter_download_401_raises(tmp_path: Path) -> None:
    adapter = DASHttpAdapter(base_url="http://das.internal:8000", token="bad")
    mock_client = _mock_http_client(401, b"Unauthorized")
    with patch("httpx.Client", return_value=mock_client):
        with pytest.raises(PermissionError):
            adapter.download_document_file("123:6", dest_dir=tmp_path)


def test_das_adapter_download_404_raises(tmp_path: Path) -> None:
    adapter = DASHttpAdapter(base_url="http://das.internal:8000", token="test-tok")
    mock_client = _mock_http_client(404, b"Not found")
    with patch("httpx.Client", return_value=mock_client):
        with pytest.raises(FileNotFoundError, match="123:6"):
            adapter.download_document_file("123:6", dest_dir=tmp_path)


# ── DAS adapter init validation ───────────────────────────────────────────────

def test_das_adapter_requires_base_url() -> None:
    with pytest.raises(ValueError, match="AI_DAS_BASE_URL"):
        DASHttpAdapter(base_url="", token="tok")


def test_das_adapter_requires_token() -> None:
    with pytest.raises(ValueError, match="AI_DAS_TOKEN"):
        DASHttpAdapter(base_url="http://das:8000", token="")


# ── API: POST /api/ai/employees/{id}/analyze (JSON adapter) ──────────────────

def test_employee_analyze_endpoint_json_adapter(tmp_path: Path) -> None:
    """POST /api/ai/employees/42/analyze с JSON адаптером возвращает 200."""
    from fastapi.testclient import TestClient
    from PIL import Image

    from app.ai.api import create_app
    from app.ai.providers import DisabledVisionProvider
    from app.ai.schemas import ExtractedField, PageExtraction
    from tests.helpers import make_repository, make_settings
    from tests.test_integration import _registry

    settings = make_settings(tmp_path)
    repo = make_repository(settings)

    doc_png = tmp_path / "id.png"
    Image.new("RGB", (200, 300), "white").save(doc_png, format="PNG")

    (tmp_path / "package_input.json").write_text(
        json.dumps({
            "employee_index": 42,
            "fio": "Иванов Иван Иванович",
            "iin": "123456789012",
            "documents": {str(6): str(doc_png)},
        }),
        encoding="utf-8",
    )

    class FakeProvider(DisabledVisionProvider):
        def extract(self, *, image_bytes, prompt) -> PageExtraction:
            return PageExtraction(
                document_type=ExtractedField(value="Удостоверение", confidence=0.9),
                full_name=ExtractedField(value="Иванов Иван Иванович", confidence=0.9),
                iin=ExtractedField(value="123456789012", confidence=0.9),
                issue_date=ExtractedField(value="01.01.2020", confidence=0.9),
                expiry_date=ExtractedField(value="01.01.2030", confidence=0.9),
            )

    app = create_app(
        settings=settings,
        repository=repo,
        provider=FakeProvider(settings),
        token_registry=_registry(operator="tok-op"),
    )

    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/employees/42/analyze",
            headers={"x-api-key": "tok-op"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["employee_id"] == "42"
    assert "analyzed_documents" in body
    assert "completeness" in body


def test_employee_analyze_requires_operator_role(tmp_path: Path) -> None:
    """viewer не может запустить employee analyze — требуется operator."""
    from fastapi.testclient import TestClient

    from app.ai.api import create_app
    from app.ai.providers import DisabledVisionProvider
    from tests.helpers import make_repository, make_settings
    from tests.test_integration import _registry

    settings = make_settings(tmp_path)
    repo = make_repository(settings)
    app = create_app(
        settings=settings,
        repository=repo,
        provider=DisabledVisionProvider(settings),
        token_registry=_registry(viewer="tok-viewer"),
    )

    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/employees/42/analyze",
            headers={"x-api-key": "tok-viewer"},
        )
    assert resp.status_code == 403
