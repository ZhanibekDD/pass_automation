from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ai_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL,
    document_id TEXT,
    source_file TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES ai_runs(id) ON DELETE CASCADE,
    employee_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    found_value TEXT,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    source_file TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK(page_number > 0),
    operator_status TEXT NOT NULL DEFAULT 'not_reviewed'
        CHECK(operator_status IN ('not_reviewed', 'confirmed', 'rejected')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES ai_runs(id) ON DELETE SET NULL,
    employee_id TEXT NOT NULL,
    document_id TEXT,
    issue_code TEXT NOT NULL,
    field_name TEXT,
    found_value TEXT,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    source_file TEXT NOT NULL DEFAULT '',
    page_number INTEGER,
    severity TEXT NOT NULL CHECK(severity IN ('low', 'medium', 'high')),
    message TEXT NOT NULL,
    operator_status TEXT NOT NULL DEFAULT 'not_reviewed'
        CHECK(operator_status IN ('not_reviewed', 'confirmed', 'rejected')),
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('extraction', 'finding')),
    entity_id INTEGER NOT NULL,
    employee_id TEXT NOT NULL,
    document_id TEXT,
    reason_code TEXT NOT NULL,
    priority TEXT NOT NULL CHECK(priority IN ('low', 'medium', 'high')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'confirmed', 'rejected')),
    operator_id TEXT,
    operator_comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    UNIQUE(entity_type, entity_id)
);

CREATE TABLE IF NOT EXISTS ai_document_fingerprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(employee_id, document_id, source_file)
);

CREATE INDEX IF NOT EXISTS idx_fingerprints_sha256
    ON ai_document_fingerprints(sha256);
CREATE INDEX IF NOT EXISTS idx_extractions_document
    ON ai_extractions(document_id, created_at);
CREATE INDEX IF NOT EXISTS idx_findings_employee
    ON ai_findings(employee_id, created_at);
CREATE INDEX IF NOT EXISTS idx_review_status
    ON ai_review_queue(status, priority, created_at);

CREATE TABLE IF NOT EXISTS ai_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('ai', 'user', 'system')),
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_value(value: str | None) -> str:
    return json.dumps(value, ensure_ascii=False)


def _read_json_value(value: str | None) -> str | None:
    if value is None:
        return None
    return json.loads(value)


class AIRepository:
    """Изолированное хранилище AI. Не подключается к production-БД."""

    def __init__(self, database_path: Path):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.database_path.parent, 0o700)
        except OSError:
            pass
        existed = self.database_path.exists()
        connection = sqlite3.connect(self.database_path, timeout=30)
        if not existed:
            try:
                os.chmod(self.database_path, 0o600)
            except OSError:
                pass
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def health(self) -> dict[str, Any]:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "schema_version": version}

    def create_run(
        self,
        *,
        employee_id: str,
        document_id: str | None,
        source_file: str,
        provider: str,
        model: str,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO ai_runs (
                    employee_id, document_id, source_file, provider, model,
                    status, started_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (employee_id, document_id, source_file, provider, model, utc_now()),
            )
            run_id = int(cursor.lastrowid)
        self.audit(
            actor_type="ai",
            actor_id=model,
            action="analysis_started",
            entity_type="run",
            entity_id=str(run_id),
            details={"employee_id": employee_id, "document_id": document_id},
        )
        return run_id

    def finish_run(self, run_id: int, status: str, error: str | None = None) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Некорректный статус запуска")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ai_runs
                SET status = ?, error = ?, completed_at = ?
                WHERE id = ?
                """,
                (status, error, utc_now(), run_id),
            )
        self.audit(
            actor_type="ai",
            actor_id="local-model",
            action=f"analysis_{status}",
            entity_type="run",
            entity_id=str(run_id),
            details={"error": error} if error else {},
        )

    def add_extraction(
        self,
        *,
        run_id: int,
        employee_id: str,
        document_id: str,
        field_name: str,
        found_value: str | None,
        confidence: float,
        source_file: str,
        page_number: int,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO ai_extractions (
                    run_id, employee_id, document_id, field_name, found_value,
                    confidence, source_file, page_number, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    employee_id,
                    document_id,
                    field_name,
                    _json_value(found_value),
                    confidence,
                    source_file,
                    page_number,
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def add_finding(
        self,
        *,
        run_id: int | None,
        employee_id: str,
        document_id: str | None,
        issue_code: str,
        field_name: str | None,
        found_value: str | None,
        confidence: float,
        source_file: str,
        page_number: int | None,
        severity: str,
        message: str,
    ) -> int:
        dedupe_source = json.dumps(
            {
                "employee_id": employee_id,
                "document_id": document_id,
                "issue_code": issue_code,
                "field_name": field_name,
                "found_value": found_value,
                "source_file": source_file,
                "page_number": page_number,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        dedupe_key = hashlib.sha256(dedupe_source.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO ai_findings (
                    run_id, employee_id, document_id, issue_code, field_name,
                    found_value, confidence, source_file, page_number, severity,
                    message, dedupe_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    employee_id,
                    document_id,
                    issue_code,
                    field_name,
                    _json_value(found_value),
                    confidence,
                    source_file,
                    page_number,
                    severity,
                    message,
                    dedupe_key,
                    utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT id FROM ai_findings WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            return int(row["id"])

    def enqueue_finding(
        self,
        *,
        finding_id: int,
        employee_id: str,
        document_id: str | None,
        reason_code: str,
        priority: str,
    ) -> int:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO ai_review_queue (
                    entity_type, entity_id, employee_id, document_id,
                    reason_code, priority, created_at
                ) VALUES ('finding', ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding_id,
                    employee_id,
                    document_id,
                    reason_code,
                    priority,
                    utc_now(),
                ),
            )
            row = connection.execute(
                """
                SELECT id FROM ai_review_queue
                WHERE entity_type = 'finding' AND entity_id = ?
                """,
                (finding_id,),
            ).fetchone()
            return int(row["id"])

    def register_fingerprint(
        self,
        *,
        employee_id: str,
        document_id: str,
        source_file: str,
        sha256: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            duplicates = connection.execute(
                """
                SELECT employee_id, document_id, source_file
                FROM ai_document_fingerprints
                WHERE sha256 = ?
                  AND NOT (employee_id = ? AND document_id = ? AND source_file = ?)
                """,
                (sha256, employee_id, document_id, source_file),
            ).fetchall()
            connection.execute(
                """
                INSERT INTO ai_document_fingerprints (
                    employee_id, document_id, source_file, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(employee_id, document_id, source_file)
                DO UPDATE SET sha256 = excluded.sha256, created_at = excluded.created_at
                """,
                (employee_id, document_id, source_file, sha256, utc_now()),
            )
        return [dict(row) for row in duplicates]

    def list_review_queue(
        self, *, status: str = "pending", limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT q.*, f.issue_code, f.field_name, f.found_value,
                       f.confidence, f.source_file, f.page_number,
                       f.message, f.severity
                FROM ai_review_queue q
                LEFT JOIN ai_findings f
                  ON q.entity_type = 'finding' AND q.entity_id = f.id
                WHERE q.status = ?
                ORDER BY
                  CASE q.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                  q.created_at ASC
                LIMIT ? OFFSET ?
                """,
                (status, limit, offset),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["found_value"] = _read_json_value(item.get("found_value"))
            result.append(item)
        return result

    def update_review(
        self,
        *,
        item_id: int,
        status: str,
        operator_id: str,
        comment: str,
    ) -> dict[str, Any] | None:
        if status not in {"confirmed", "rejected"}:
            raise ValueError("Некорректный статус ручной проверки")
        with self._connect() as connection:
            item = connection.execute(
                "SELECT * FROM ai_review_queue WHERE id = ?", (item_id,)
            ).fetchone()
            if item is None:
                return None
            connection.execute(
                """
                UPDATE ai_review_queue
                SET status = ?, operator_id = ?, operator_comment = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (status, operator_id, comment, utc_now(), item_id),
            )
            entity_table = (
                "ai_findings" if item["entity_type"] == "finding" else "ai_extractions"
            )
            connection.execute(
                f"UPDATE {entity_table} SET operator_status = ? WHERE id = ?",
                (status, item["entity_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM ai_review_queue WHERE id = ?", (item_id,)
            ).fetchone()
        self.audit(
            actor_type="user",
            actor_id=operator_id,
            action=f"review_{status}",
            entity_type="review_item",
            entity_id=str(item_id),
            details={"comment": comment},
        )
        return dict(updated)

    def document_analysis(self, document_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            runs = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM ai_runs
                    WHERE document_id = ?
                    ORDER BY started_at DESC
                    """,
                    (document_id,),
                ).fetchall()
            ]
            extractions = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM ai_extractions
                    WHERE document_id = ?
                    ORDER BY created_at DESC, page_number, field_name
                    """,
                    (document_id,),
                ).fetchall()
            ]
            findings = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM ai_findings
                    WHERE document_id = ?
                    ORDER BY created_at DESC
                    """,
                    (document_id,),
                ).fetchall()
            ]
        for item in extractions:
            item["found_value"] = _read_json_value(item["found_value"])
        for item in findings:
            item["found_value"] = _read_json_value(item["found_value"])
            item.pop("dedupe_key", None)
        return {
            "document_id": document_id,
            "runs": runs,
            "extractions": extractions,
            "findings": findings,
        }

    def employee_findings(self, employee_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ai_findings
                WHERE employee_id = ?
                ORDER BY created_at DESC
                """,
                (employee_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["found_value"] = _read_json_value(item["found_value"])
            item.pop("dedupe_key", None)
            result.append(item)
        return result

    def summary(self) -> dict[str, Any]:
        with self._connect() as connection:
            runs = connection.execute(
                "SELECT status, COUNT(*) count FROM ai_runs GROUP BY status"
            ).fetchall()
            queue = connection.execute(
                "SELECT status, COUNT(*) count FROM ai_review_queue GROUP BY status"
            ).fetchall()
            issues = connection.execute(
                """
                SELECT issue_code, COUNT(*) count
                FROM ai_findings
                GROUP BY issue_code
                ORDER BY count DESC
                """
            ).fetchall()
            last_run = connection.execute(
                "SELECT MAX(started_at) AS value FROM ai_runs"
            ).fetchone()["value"]
        return {
            "runs": {row["status"]: row["count"] for row in runs},
            "review_queue": {row["status"]: row["count"] for row in queue},
            "findings": {row["issue_code"]: row["count"] for row in issues},
            "last_run_at": last_run,
        }

    def audit(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_audit_log (
                    actor_type, actor_id, action, entity_type, entity_id,
                    details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actor_type,
                    actor_id,
                    action,
                    entity_type,
                    entity_id,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )

    def audit_entries(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_audit_log ORDER BY id"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result
