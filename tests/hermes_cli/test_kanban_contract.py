from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def contract_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _contract(**overrides):
    value = {
        "goal_id": "goal-reside-don",
        "business_domain": "reside",
        "source_system": "ghl",
        "source_account": "reside-location",
        "deliverable": "new-leads-only setter queue",
        "recipient_ids": ["12345"],
        "approval": {
            "approved_by": "suneet",
            "message_id": "discord-approval-1",
            "action": "share filtered names with Don",
        },
    }
    value.update(overrides)
    return value


def test_contract_persists_and_automatically_deduplicates(contract_home):
    conn = kb.connect()
    try:
        first = kb.create_task(
            conn, title="build list", assignee="dave", contract=_contract()
        )
        second = kb.create_task(
            conn, title="build the same list again", assignee="dave", contract=_contract()
        )
        assert second == first
        task = kb.get_task(conn, first)
        assert task is not None
        assert task.contract == _contract()
        assert task.idempotency_key is not None
        assert task.idempotency_key.startswith("contract:")
    finally:
        conn.close()


def test_child_inherits_contract_and_cannot_change_scope(contract_home):
    conn = kb.connect()
    try:
        parent = kb.create_task(
            conn, title="root", assignee="dispatch", contract=_contract()
        )
        child = kb.create_task(
            conn, title="worker", assignee="dave", parents=[parent]
        )
        child_task = kb.get_task(conn, child)
        assert child_task is not None
        assert child_task.contract == _contract()

        with pytest.raises(ValueError, match="cannot change inherited goal scope"):
            kb.create_task(
                conn,
                title="wrong source",
                assignee="dave",
                parents=[parent],
                contract=_contract(business_domain="bshg", source_system="fub"),
            )
    finally:
        conn.close()


def test_completion_rejects_wrong_or_missing_source_metadata(contract_home):
    conn = kb.connect()
    try:
        tid = kb.create_task(
            conn, title="source-bound", assignee="dave", contract=_contract()
        )
        with pytest.raises(ValueError, match="task contract validation failed"):
            kb.complete_task(
                conn,
                tid,
                summary="wrong CRM result",
                metadata={
                    "business_domain": "bshg",
                    "source_system": "fub",
                    "source_account": "bshg-account",
                },
            )
        rejected = kb.get_task(conn, tid)
        assert rejected is not None
        assert rejected.status != "done"

        assert kb.complete_task(
            conn,
            tid,
            summary="correct source result",
            metadata={
                "business_domain": "reside",
                "source_system": "ghl",
                "source_account": "reside-location",
            },
        )
    finally:
        conn.close()


def test_consequential_contract_requires_review(contract_home):
    conn = kb.connect()
    try:
        contract = _contract(requires_review=True)
        tid = kb.create_task(
            conn, title="review me", assignee="dave", contract=contract
        )
        metadata = {
            "business_domain": "reside",
            "source_system": "ghl",
            "source_account": "reside-location",
        }
        with pytest.raises(ValueError, match="requires independent review"):
            kb.complete_task(conn, tid, summary="worker says done", metadata=metadata)

        assert kb.request_review(
            conn, tid, summary="ready for Marshal", metadata=metadata, reviewer="marshal"
        )
        in_review = kb.get_task(conn, tid)
        assert in_review is not None
        assert in_review.status == "review"
        assert kb.complete_task(
            conn, tid, summary="Marshal verified", metadata=metadata
        )
        done = kb.get_task(conn, tid)
        assert done is not None
        assert done.status == "done"
    finally:
        conn.close()


def test_contract_requires_core_scope_fields(contract_home):
    conn = kb.connect()
    try:
        bad = _contract()
        del bad["source_system"]
        with pytest.raises(ValueError, match="source_system"):
            kb.create_task(conn, title="bad contract", assignee="dave", contract=bad)
    finally:
        conn.close()
