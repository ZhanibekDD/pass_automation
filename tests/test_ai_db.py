from app.ai.db import AIRepository


def test_repository_stores_provenance_and_operator_confirmation(tmp_path) -> None:
    repository = AIRepository(tmp_path / "ai.sqlite3")
    repository.initialize()
    run_id = repository.create_run(
        employee_id="42",
        document_id="42:6",
        source_file="passport.png",
        provider="ollama",
        model="vision-test",
    )
    repository.add_extraction(
        run_id=run_id,
        employee_id="42",
        document_id="42:6",
        field_name="iin",
        found_value="1111",
        confidence=0.82,
        source_file="passport.png",
        page_number=1,
    )
    finding_id = repository.add_finding(
        run_id=run_id,
        employee_id="42",
        document_id="42:6",
        issue_code="iin_mismatch",
        field_name="iin",
        found_value="1111",
        confidence=0.82,
        source_file="passport.png",
        page_number=1,
        severity="high",
        message="ИИН не совпадает",
    )
    queue_id = repository.enqueue_finding(
        finding_id=finding_id,
        employee_id="42",
        document_id="42:6",
        reason_code="iin_mismatch",
        priority="high",
    )
    repository.finish_run(run_id, "completed")

    pending = repository.list_review_queue()
    assert pending[0]["employee_id"] == "42"
    assert pending[0]["document_id"] == "42:6"
    assert pending[0]["found_value"] == "1111"
    assert pending[0]["source_file"] == "passport.png"
    assert pending[0]["page_number"] == 1

    updated = repository.update_review(
        item_id=queue_id,
        status="confirmed",
        operator_id="operator-1",
        comment="Сверено с оригиналом",
    )
    assert updated is not None
    assert updated["status"] == "confirmed"
    assert any(
        entry["actor_type"] == "user" and entry["action"] == "review_confirmed"
        for entry in repository.audit_entries()
    )


def test_fingerprint_detects_duplicate_without_storing_file(tmp_path) -> None:
    repository = AIRepository(tmp_path / "ai.sqlite3")
    repository.initialize()
    assert (
        repository.register_fingerprint(
            employee_id="1",
            document_id="1:6",
            source_file="first.pdf",
            sha256="a" * 64,
        )
        == []
    )
    duplicates = repository.register_fingerprint(
        employee_id="2",
        document_id="2:6",
        source_file="second.pdf",
        sha256="a" * 64,
    )
    assert duplicates == [
        {
            "employee_id": "1",
            "document_id": "1:6",
            "source_file": "first.pdf",
        }
    ]
