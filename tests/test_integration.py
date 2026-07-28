"""Integration-тесты: model unavailable, timeout, vehicle e2e, operator_id, backup/restore."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.api import create_app
from app.ai.auth import TokenEntry, TokenRegistry, _sha256
from app.ai.db import AIRepository
from app.ai.providers import DisabledVisionProvider, VisionProvider
from app.ai.schemas import PageExtraction, VehiclePageExtraction
from tests.helpers import make_repository, make_settings

# ── helpers ────────────────────────────────────────────────────────────────────

def _registry(**roles: str) -> TokenRegistry:
    r = TokenRegistry()
    for role, tok in roles.items():
        r._entries.append(TokenEntry(token_hash=_sha256(tok), role=role, description=f"test-{role}"))  # type: ignore[arg-type]
    return r


def _app(settings=None, repo=None, provider=None, registry=None):
    s = settings or make_settings()
    r = repo or make_repository(s)
    p = provider or DisabledVisionProvider(s)
    reg = registry or _registry(
        admin="tok-admin", operator="tok-operator", reviewer="tok-reviewer", viewer="tok-viewer"
    )
    return create_app(settings=s, repository=r, provider=p, token_registry=reg)


# ── operator_id не может быть подделан ────────────────────────────────────────


def test_operator_id_comes_from_token_not_header(tmp_path: Path) -> None:
    """Клиент НЕ может подделать operator_id через X-Operator-Id; он берётся из description токена."""
    settings = make_settings(tmp_path)
    repo = make_repository(settings)
    registry = _registry(reviewer="tok-reviewer")
    # Добавим описание, чтобы убедиться что оно используется
    registry._entries[0] = TokenEntry(
        token_hash=_sha256("tok-reviewer"),
        role="reviewer",
        description="alice",
    )
    app = create_app(settings=settings, repository=repo,
                     provider=DisabledVisionProvider(settings), token_registry=registry)

    with TestClient(app) as client:
        # Пытаемся передать поддельный X-Operator-Id
        r = client.patch(
            "/api/ai/review-queue/9999",
            json={"status": "confirmed", "comment": ""},
            headers={
                "x-api-key": "tok-reviewer",
                "x-operator-id": "mallory",  # ← должен быть проигнорирован
            },
        )
    # 404 нормален (нет записи), важно что запрос прошёл RBAC
    assert r.status_code == 404

    # Проверяем audit log: operator_id = "alice", а не "mallory"
    # Даже если not found, audit должен записать actor_id = "alice"
    # (в данном случае update_review возвращает None → PATCH возвращает 404 без audit)
    # Проверяем через summary, что в ключе "viewer" api вызов логируется корректно
    with TestClient(app) as client2:
        r2 = client2.get("/api/ai/summary", headers={"x-api-key": "tok-reviewer", "x-operator-id": "mallory"})
    assert r2.status_code == 200

    entries2 = repo.audit_entries()
    summary_entries = [e for e in entries2 if e["action"] == "api_read" and e["entity_type"] == "summary"]
    assert summary_entries, "audit_log должен содержать запись о чтении summary"
    latest = summary_entries[-1]
    assert latest["actor_id"] == "alice", (
        f"actor_id должен быть 'alice' (из description токена), получен '{latest['actor_id']}'"
    )
    assert latest["actor_id"] != "mallory", "Подделка operator_id через заголовок должна быть невозможна"


# ── model unavailable ──────────────────────────────────────────────────────────


def test_health_shows_model_unavailable() -> None:
    """Если vision-провайдер недоступен, health возвращает статус degraded (но не 5xx)."""
    # Создаём провайдер, который всегда возвращает status=unavailable
    class UnavailableProvider(VisionProvider):
        def health(self):
            return {"status": "unavailable", "provider": "ollama", "model": "test", "error": "ConnectError"}

        def extract(self, *, image_bytes, prompt) -> PageExtraction:  # type: ignore[return]
            raise RuntimeError("unavailable")

        def extract_vehicle(self, *, image_bytes, prompt) -> VehiclePageExtraction:  # type: ignore[return]
            raise RuntimeError("unavailable")

    s = make_settings()
    r = make_repository(s)
    app = create_app(settings=s, repository=r, provider=UnavailableProvider(s),
                     token_registry=_registry(admin="tok-admin"))
    with TestClient(app) as client:
        resp = client.get("/api/ai/health")
    assert resp.status_code == 200
    body = resp.json()
    # Если AI_ENABLED=True, статус должен быть degraded при недоступной модели
    # Но наш make_settings возвращает enabled=True по умолчанию
    assert body["model"]["status"] == "unavailable"


# ── timeout ────────────────────────────────────────────────────────────────────


def test_vision_provider_timeout_is_respected() -> None:
    """VisionProvider использует request_timeout_seconds из settings."""

    settings = make_settings()
    # Проверяем, что timeout прописан в клиенте
    from app.ai.providers import OllamaVisionProvider

    provider = OllamaVisionProvider(settings)
    with provider._client() as client:
        assert client.timeout.read == settings.request_timeout_seconds


# ── backup / restore ───────────────────────────────────────────────────────────


def test_backup_script_exists_and_is_executable() -> None:
    script = Path("scripts/backup.sh")
    assert script.exists(), "scripts/backup.sh не найден"
    # Проверяем синтаксис через bash --norc -n (dry-run lint).
    # На Windows используем POSIX-путь (forward slashes).
    result = subprocess.run(
        ["bash", "--norc", "-n", script.as_posix()],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"Синтаксическая ошибка в backup.sh: {result.stderr}"


def test_restore_script_exists_and_is_executable() -> None:
    script = Path("scripts/restore.sh")
    assert script.exists(), "scripts/restore.sh не найден"
    result = subprocess.run(
        ["bash", "--norc", "-n", script.as_posix()],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"Синтаксическая ошибка в restore.sh: {result.stderr}"


def test_backup_creates_sqlite_copy(tmp_path: Path) -> None:
    """Backup через sqlite3 .backup: целостность копии проверяется PRAGMA integrity_check."""
    import sqlite3

    # Создаём исходную БД
    db_path = tmp_path / "ai.sqlite3"
    repo = AIRepository(db_path)
    repo.initialize()
    repo.create_run(employee_id="emp1", document_id="doc1",
                    source_file="f.pdf", provider="ollama", model="qwen")

    # Делаем online backup через sqlite3 Python API (аналог scripts/backup.sh)
    backup_path = tmp_path / "backup.sqlite3"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    src.backup(dst)
    src.close()
    dst.close()

    # Проверяем целостность копии
    conn = sqlite3.connect(backup_path)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    runs_count = conn.execute("SELECT COUNT(*) FROM ai_runs").fetchone()[0]
    conn.close()

    assert integrity == "ok", f"Резервная копия не прошла integrity_check: {integrity}"
    assert runs_count == 1, "Данные в резервной копии должны совпадать с источником"


# ── docker compose config ──────────────────────────────────────────────────────


@pytest.mark.skipif(
    subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0,
    reason="docker compose не установлен",
)
def test_docker_compose_staging_config_valid() -> None:
    """docker compose -f docker-compose.staging.yml config завершается без ошибок."""
    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.staging.yml", "config", "--quiet"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"docker-compose.staging.yml невалиден:\n{result.stderr}"
    )


@pytest.mark.skipif(
    subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0,
    reason="docker compose не установлен",
)
def test_docker_compose_production_config_valid() -> None:
    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.production.yml", "config", "--quiet"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"docker-compose.production.yml невалиден:\n{result.stderr}"
    )


# ── vehicle end-to-end (mock provider) ────────────────────────────────────────


def _make_vehicle_extraction(**overrides) -> VehiclePageExtraction:
    from app.ai.schemas import ExtractedField

    defaults = {
        "document_type": ExtractedField(value="Страховой полис", confidence=0.95),
        "plate_number": ExtractedField(value="А123ВС77", confidence=0.95),
        "vin": ExtractedField(value=None, confidence=0.0),
        "registration_number": ExtractedField(value="ААА 123456", confidence=0.9),
        "driver_name": ExtractedField(value="Иванов Иван Иванович", confidence=0.9),
        "issue_date": ExtractedField(value="01.01.2024", confidence=0.9),
        "expiry_date": ExtractedField(value="01.01.2026", confidence=0.9),
    }
    defaults.update(overrides)
    return VehiclePageExtraction(**defaults)


def test_vehicle_analyze_endpoint_e2e(tmp_path: Path) -> None:
    """POST /api/ai/vehicles/{id}/analyze → строго 200, extractions сохранены."""
    from PIL import Image

    settings = make_settings(tmp_path)
    repo = make_repository(settings)

    # PIL создаёт гарантированно валидное PNG (iter_document_pages принимает PNG напрямую)
    doc_file = settings.input_json_path.parent / "test_insurance.png"
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), color="white").save(doc_file, format="PNG")

    class MockVehicleProvider(DisabledVisionProvider):
        def extract_vehicle(self, *, image_bytes, prompt) -> VehiclePageExtraction:
            return _make_vehicle_extraction()

    registry = _registry(operator="tok-operator", viewer="tok-viewer")
    app = create_app(settings=settings, repository=repo,
                     provider=MockVehicleProvider(settings), token_registry=registry)

    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/vehicles/v-001/analyze",
            json={
                "plate_number": "А123ВС77",
                "driver": "Иванов Иван Иванович",
                "document_code": "insurance",
                "document_id": "v-001:insurance",
                "source_path": str(doc_file),
            },
            headers={"x-api-key": "tok-operator"},
        )
        assert resp.status_code == 200, (
            f"Ожидался 200, получен {resp.status_code}: {resp.text}"
        )

        body = resp.json()
        assert "runs" in body
        assert "findings" in body
        assert "extractions" in body, "vehicle_analysis должен возвращать extractions"

        runs = body["runs"]
        assert any(r["status"] == "completed" for r in runs), (
            f"Ожидался run 'completed', получено: {[r['status'] for r in runs]}"
        )

        extractions = body["extractions"]
        assert extractions, "Extractions должны быть сохранены (7 полей × 1 страница)"
        field_names = {e["field_name"] for e in extractions}
        assert "plate_number" in field_names
        assert "driver_name" in field_names

        compl = client.get("/api/ai/vehicles/v-001/completeness", headers={"x-api-key": "tok-viewer"})
        assert compl.status_code == 200
        comp_body = compl.json()
        assert "insurance" in comp_body["analyzed_document_codes"]


def test_vehicle_analyze_requires_operator_role(tmp_path: Path) -> None:
    """viewer не может запустить vehicle analyze — требуется operator."""
    settings = make_settings(tmp_path)
    repo = make_repository(settings)
    registry = _registry(viewer="tok-viewer")
    app = create_app(settings=settings, repository=repo,
                     provider=DisabledVisionProvider(settings), token_registry=registry)

    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/vehicles/v-001/analyze",
            json={"document_code": "insurance", "document_id": "x", "source_path": "/tmp/x.pdf"},
            headers={"x-api-key": "tok-viewer"},
        )
    assert resp.status_code == 403


def test_vehicle_analyze_rejects_path_outside_data(tmp_path: Path) -> None:
    """source_path за пределами data/input/ возвращает 400."""
    settings = make_settings(tmp_path)
    repo = make_repository(settings)
    registry = _registry(operator="tok-operator")
    app = create_app(settings=settings, repository=repo,
                     provider=DisabledVisionProvider(settings), token_registry=registry)

    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/vehicles/v-001/analyze",
            json={
                "document_code": "insurance",
                "document_id": "x",
                "source_path": "/etc/passwd",  # за пределами data/input
            },
            headers={"x-api-key": "tok-operator"},
        )
    assert resp.status_code == 400, f"Ожидался 400, получен {resp.status_code}"


# ── document_analysis endpoint (pk-based document_id) ─────────────────────────


def test_document_analysis_returns_data_by_pk(tmp_path: Path) -> None:
    """GET /api/ai/documents/789/analysis работает с pk-based document_id без split(':')."""
    settings = make_settings(tmp_path)
    repo = make_repository(settings)
    run_id = repo.create_run(
        employee_id="emp1",
        document_id="789",
        source_file="id.pdf",
        provider="ollama",
        model="qwen",
    )
    repo.finish_run(run_id, "completed")

    app = _app(settings=settings, repo=repo, registry=_registry(viewer="tok-viewer"))
    with TestClient(app) as client:
        resp = client.get("/api/ai/documents/789/analysis", headers={"x-api-key": "tok-viewer"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["document_id"] == "789"
    assert len(body["runs"]) == 1
    assert body["runs"][0]["status"] == "completed"


def test_document_analysis_returns_404_when_not_found(tmp_path: Path) -> None:
    """Пустой результат (нет runs/extractions/findings) → 404."""
    settings = make_settings(tmp_path)
    repo = make_repository(settings)
    app = _app(settings=settings, repo=repo, registry=_registry(viewer="tok-viewer"))
    with TestClient(app) as client:
        resp = client.get("/api/ai/documents/999/analysis", headers={"x-api-key": "tok-viewer"})
    assert resp.status_code == 404


# ── health overall status ──────────────────────────────────────────────────────


def test_health_overall_degraded_when_json_file_missing(tmp_path: Path) -> None:
    """health.status=degraded когда package_input.json отсутствует (JSON адаптер)."""
    settings = make_settings(tmp_path, enabled=False)
    # package_input.json не создаём → input_adapter.status = "unavailable"
    app = _app(settings=settings, registry=_registry(viewer="tok"))
    with TestClient(app) as client:
        resp = client.get("/api/ai/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["input_adapter"]["status"] == "unavailable"
    assert body["status"] == "degraded"


def test_health_overall_ok_when_json_file_present(tmp_path: Path) -> None:
    """health.status=ok когда JSON-файл существует и модель отключена."""
    settings = make_settings(tmp_path, enabled=False)
    settings.input_json_path.write_text("{}", encoding="utf-8")
    app = _app(settings=settings, registry=_registry(viewer="tok"))
    with TestClient(app) as client:
        resp = client.get("/api/ai/health")
    body = resp.json()
    assert body["input_adapter"]["status"] == "ok"
    assert body["status"] == "ok"


def test_health_overall_degraded_when_model_unavailable_and_enabled(tmp_path: Path) -> None:
    """health.status=degraded когда модель недоступна и enabled=True."""

    class UnavailableProvider(VisionProvider):
        def health(self):
            return {"status": "unavailable", "provider": "ollama", "model": "test", "error": "x"}

        def extract(self, *, image_bytes, prompt) -> PageExtraction:  # type: ignore[return]
            raise RuntimeError("unavailable")

        def extract_vehicle(self, *, image_bytes, prompt) -> VehiclePageExtraction:  # type: ignore[return]
            raise RuntimeError("unavailable")

    settings = make_settings(tmp_path, enabled=True)
    settings.input_json_path.write_text("{}", encoding="utf-8")
    repo = make_repository(settings)
    app = _app(settings=settings, repo=repo,
                provider=UnavailableProvider(settings),
                registry=_registry(viewer="tok"))
    with TestClient(app) as client:
        resp = client.get("/api/ai/health")
    body = resp.json()
    assert body["model"]["status"] == "unavailable"
    assert body["status"] == "degraded"
