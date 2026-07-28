"""Тесты RBAC: 401, 403, роли, PATCH только reviewer/admin."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ai.api import create_app
from app.ai.auth import TokenEntry, TokenRegistry, _sha256
from app.ai.providers import DisabledVisionProvider
from tests.helpers import make_repository, make_settings


def _make_registry(**roles: str) -> TokenRegistry:
    registry = TokenRegistry()
    for role, token in roles.items():
        registry._entries.append(
            TokenEntry(token_hash=_sha256(token), role=role, description=role)  # type: ignore[arg-type]
        )
    return registry


def _client_with_registry(registry: TokenRegistry) -> TestClient:
    settings = make_settings()
    repo = make_repository(settings)
    provider = DisabledVisionProvider(settings)
    app = create_app(
        settings=settings,
        repository=repo,
        provider=provider,
        token_registry=registry,
    )
    return TestClient(app, raise_server_exceptions=True)


REGISTRY = _make_registry(
    viewer="tok-viewer",
    operator="tok-operator",
    reviewer="tok-reviewer",
    admin="tok-admin",
)


@pytest.fixture()
def client() -> TestClient:
    return _client_with_registry(REGISTRY)


# ------------------------------------------------------------------ health открыт


def test_health_no_auth(client: TestClient) -> None:
    r = client.get("/api/ai/health")
    assert r.status_code == 200
    assert r.json()["shadow_mode"] is True


# ------------------------------------------------------------------ 401 без ключа


@pytest.mark.parametrize(
    "url",
    [
        "/api/ai/summary",
        "/api/ai/review-queue",
        "/api/ai/vehicles/summary",
        "/api/ai/vehicles/review-queue",
        "/api/ai/audit-log",
    ],
)
def test_no_key_returns_401(client: TestClient, url: str) -> None:
    r = client.get(url)
    assert r.status_code == 401


def test_wrong_key_returns_401(client: TestClient) -> None:
    r = client.get("/api/ai/summary", headers={"x-api-key": "bad-key"})
    assert r.status_code == 401


# ------------------------------------------------------------------ 403 недостаточно прав


def test_viewer_cannot_patch_review_queue(client: TestClient) -> None:
    r = client.patch(
        "/api/ai/review-queue/1",
        json={"status": "confirmed", "comment": "ok"},
        headers={"x-api-key": "tok-viewer"},
    )
    assert r.status_code == 403


def test_operator_cannot_patch_review_queue(client: TestClient) -> None:
    r = client.patch(
        "/api/ai/review-queue/1",
        json={"status": "confirmed", "comment": "ok"},
        headers={"x-api-key": "tok-operator"},
    )
    assert r.status_code == 403


def test_viewer_cannot_access_audit_log(client: TestClient) -> None:
    r = client.get("/api/ai/audit-log", headers={"x-api-key": "tok-viewer"})
    assert r.status_code == 403


def test_reviewer_cannot_access_audit_log(client: TestClient) -> None:
    r = client.get("/api/ai/audit-log", headers={"x-api-key": "tok-reviewer"})
    assert r.status_code == 403


# ------------------------------------------------------------------ допустимые роли


def test_viewer_can_read_summary(client: TestClient) -> None:
    r = client.get("/api/ai/summary", headers={"x-api-key": "tok-viewer"})
    assert r.status_code == 200


def test_viewer_can_read_review_queue(client: TestClient) -> None:
    r = client.get("/api/ai/review-queue", headers={"x-api-key": "tok-viewer"})
    assert r.status_code == 200


def test_reviewer_can_patch_review_queue_not_found(client: TestClient) -> None:
    r = client.patch(
        "/api/ai/review-queue/9999",
        json={"status": "confirmed", "comment": "test"},
        headers={"x-api-key": "tok-reviewer"},
    )
    assert r.status_code == 404


def test_admin_can_read_audit_log(client: TestClient) -> None:
    r = client.get("/api/ai/audit-log", headers={"x-api-key": "tok-admin"})
    assert r.status_code == 200


# ------------------------------------------------------------------ пустой реестр


def test_empty_registry_returns_503() -> None:
    settings = make_settings()
    repo = make_repository(settings)
    provider = DisabledVisionProvider(settings)
    app = create_app(
        settings=settings,
        repository=repo,
        provider=provider,
        token_registry=TokenRegistry(),  # пустой
    )
    with TestClient(app) as c:
        r = c.get("/api/ai/summary", headers={"x-api-key": "anything"})
    assert r.status_code == 503


# ------------------------------------------------------------------ vehicle endpoints


def test_viewer_can_read_vehicle_summary(client: TestClient) -> None:
    r = client.get("/api/ai/vehicles/summary", headers={"x-api-key": "tok-viewer"})
    assert r.status_code == 200


def test_viewer_cannot_patch_vehicle_queue(client: TestClient) -> None:
    r = client.patch(
        "/api/ai/vehicles/review-queue/1",
        json={"status": "confirmed"},
        headers={"x-api-key": "tok-viewer"},
    )
    assert r.status_code == 403


def test_reviewer_can_patch_vehicle_queue_not_found(client: TestClient) -> None:
    r = client.patch(
        "/api/ai/vehicles/review-queue/9999",
        json={"status": "rejected", "comment": "wrong doc"},
        headers={"x-api-key": "tok-reviewer"},
    )
    assert r.status_code == 404
