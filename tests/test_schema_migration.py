"""Тест миграции SQLite: обновление существующей phase-1 БД (v1) до v2."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.ai.db import SCHEMA_V1_SQL, SCHEMA_VERSION, AIRepository


def _create_v1_db(db_path: Path) -> None:
    """Создаёт базу данных версии 1 (phase-1) без ip_address/role и без таблиц ТС."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_V1_SQL)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


def _column_names(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    conn.close()
    return cols


def _table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    return names


# ------------------------------------------------------------------ v1 → v2


def test_v1_to_v2_adds_audit_columns(tmp_path: Path) -> None:
    """После миграции v1→v2 в ai_audit_log появляются ip_address и role."""
    db_path = tmp_path / "phase1.sqlite3"
    _create_v1_db(db_path)

    # Проверяем, что в v1 этих столбцов нет
    assert "ip_address" not in _column_names(db_path, "ai_audit_log")
    assert "role" not in _column_names(db_path, "ai_audit_log")

    # Запускаем миграцию через AIRepository.initialize()
    repo = AIRepository(db_path)
    repo.initialize()

    cols = _column_names(db_path, "ai_audit_log")
    assert "ip_address" in cols, "ip_address не добавлен в ai_audit_log после миграции v1→v2"
    assert "role" in cols, "role не добавлен в ai_audit_log после миграции v1→v2"


def test_v1_to_v2_creates_vehicle_tables(tmp_path: Path) -> None:
    """После миграции v1→v2 создаются таблицы ai_vehicle_*."""
    db_path = tmp_path / "phase1.sqlite3"
    _create_v1_db(db_path)

    before = _table_names(db_path)
    assert "ai_vehicle_runs" not in before

    AIRepository(db_path).initialize()

    after = _table_names(db_path)
    assert "ai_vehicle_runs" in after, "ai_vehicle_runs не создана при миграции v1→v2"
    assert "ai_vehicle_findings" in after
    assert "ai_vehicle_review_queue" in after


def test_v1_to_v2_sets_correct_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "phase1.sqlite3"
    _create_v1_db(db_path)

    AIRepository(db_path).initialize()

    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == SCHEMA_VERSION, f"user_version должен быть {SCHEMA_VERSION}, получен {version}"


def test_migration_idempotent(tmp_path: Path) -> None:
    """Повторный вызов initialize() на v2 БД не ломает и не меняет user_version."""
    db_path = tmp_path / "phase1.sqlite3"
    _create_v1_db(db_path)

    repo = AIRepository(db_path)
    repo.initialize()
    repo.initialize()  # второй вызов — идемпотентный

    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == SCHEMA_VERSION


def test_migration_preserves_existing_data(tmp_path: Path) -> None:
    """Данные, записанные до миграции, доступны после неё."""
    db_path = tmp_path / "phase1.sqlite3"
    _create_v1_db(db_path)

    # Записываем данные в v1
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ai_runs (employee_id, source_file, provider, model, status, started_at)"
        " VALUES ('emp1', 'f.pdf', 'ollama', 'qwen', 'completed', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO ai_audit_log (actor_type, actor_id, action, entity_type, details_json, created_at)"
        " VALUES ('ai', 'qwen', 'test', 'run', '{}', '2024-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    # Миграция
    AIRepository(db_path).initialize()

    # Данные сохранились
    conn2 = sqlite3.connect(db_path)
    runs = conn2.execute("SELECT COUNT(*) FROM ai_runs").fetchone()[0]
    audits = conn2.execute("SELECT COUNT(*) FROM ai_audit_log").fetchone()[0]
    # ip_address и role для старых строк — пустые строки (DEFAULT '')
    ip_col = conn2.execute("SELECT ip_address FROM ai_audit_log LIMIT 1").fetchone()[0]
    conn2.close()

    assert runs == 1, "Запись в ai_runs потеряна при миграции"
    assert audits == 1, "Запись в ai_audit_log потеряна при миграции"
    assert ip_col == "", "ip_address должен быть пустой строкой для старых строк"


def test_fresh_db_gets_correct_version(tmp_path: Path) -> None:
    """Новая база данных получает SCHEMA_VERSION без миграций."""
    db_path = tmp_path / "fresh.sqlite3"
    AIRepository(db_path).initialize()

    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == SCHEMA_VERSION


def test_vehicle_completeness_uses_document_code(tmp_path: Path) -> None:
    """vehicle_completeness сравнивает document_code, а не document_id."""
    db_path = tmp_path / "ai.sqlite3"
    repo = AIRepository(db_path)
    repo.initialize()

    run_id = repo.create_vehicle_run(
        vehicle_id="v1",
        document_id="v1:insurance",
        document_code="insurance",  # ← code, не id
        source_file="ins.pdf",
        provider="ollama",
        model="qwen",
    )
    repo.finish_vehicle_run(run_id, "completed")

    result = repo.vehicle_completeness("v1", required_codes=["insurance", "registration", "inspection"])
    assert "insurance" in result["analyzed_document_codes"]
    # registration и inspection ещё не обработаны
    assert set(result["missing_documents"]) == {"registration", "inspection"}
    assert result["is_complete"] is False


@pytest.mark.parametrize("required", [["registration"], ["insurance", "inspection"]])
def test_vehicle_completeness_all_present(tmp_path: Path, required: list[str]) -> None:
    db_path = tmp_path / "ai.sqlite3"
    repo = AIRepository(db_path)
    repo.initialize()

    for code in required:
        run_id = repo.create_vehicle_run(
            vehicle_id="v2",
            document_id=f"v2:{code}",
            document_code=code,
            source_file=f"{code}.pdf",
            provider="ollama",
            model="qwen",
        )
        repo.finish_vehicle_run(run_id, "completed")

    result = repo.vehicle_completeness("v2", required_codes=required)
    assert result["missing_documents"] == []
    assert result["is_complete"] is True
