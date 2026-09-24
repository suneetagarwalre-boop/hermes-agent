"""Contract completion must authorize the current review run under the write lock."""
import json
import uuid
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


META = {"business_domain": "infrastructure", "source_system": "test-ledger"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Prove isolation before init_db (which writes) even in worker environments.
    for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_HOME", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(kb, "_fire_kanban_lifecycle_hook", lambda *a, **k: None)
    monkeypatch.setattr(kb, "_cleanup_workspace", lambda *a, **k: None)
    kb.init_db()
    conn = kb.connect()
    assert Path(conn.execute("PRAGMA database_list").fetchone()[2]).is_relative_to(tmp_path)
    yield conn
    conn.close()


def task(db, *, review=True, claimed=True, parents=()):
    tid = kb.create_task(db, title="review provenance", assignee="builder", parents=parents,
                         contract={**META, "goal_id": str(uuid.uuid4()),
                                   "deliverable": "tested fix", "requires_review": True})
    if review:
        assert kb.request_review(db, tid, summary="implementation", metadata=META, reviewer="marshal")
    if claimed:
        claim = kb.claim_review_task(db, tid) if review else kb.claim_task(db, tid)
        assert claim is not None
    result = kb.get_task(db, tid)
    assert result is not None
    return result


def completed_events(db, tid):
    return db.execute("SELECT count(*) FROM task_events WHERE task_id=? AND kind='completed'", (tid,)).fetchone()[0]


@pytest.mark.parametrize("claimed", [False, True])
def test_parked_and_claimed_review_complete(db, claimed):
    t = task(db, claimed=claimed)
    assert kb.complete_task(db, t.id, summary="review approved", metadata=META,
                            expected_run_id=t.current_run_id)
    assert kb.get_task(db, t.id).status == "done"
    assert completed_events(db, t.id) == 1


@pytest.mark.parametrize("claimed", [False, True])
def test_unreviewed_implementation_rejected(db, claimed):
    t = task(db, review=False, claimed=claimed)
    with pytest.raises(ValueError, match="requires independent review"):
        kb.complete_task(db, t.id, metadata=META, expected_run_id=t.current_run_id)
    assert kb.get_task(db, t.id).status == t.status
    assert completed_events(db, t.id) == 0


def test_stale_expected_run_is_noop(db):
    t = task(db)
    assert not kb.complete_task(db, t.id, metadata=META, expected_run_id=t.current_run_id + 1)
    assert kb.get_task(db, t.id).current_run_id == t.current_run_id
    assert completed_events(db, t.id) == 0


@pytest.mark.parametrize("payload", [None, "bad-json", "[]", "null", '{}', '{"source_status":"ready"}'])
def test_malformed_or_missing_provenance_fails_closed(db, payload):
    t = task(db)
    with kb.write_txn(db):
        db.execute("UPDATE task_events SET payload=? WHERE task_id=? AND run_id=? AND kind='claimed'",
                   (payload, t.id, t.current_run_id))
    with pytest.raises(ValueError, match="requires independent review"):
        kb.complete_task(db, t.id, metadata={**META, "source_status": "review"})
    assert kb.get_task(db, t.id).status == "running"
    assert completed_events(db, t.id) == 0


def test_absent_claim_event_fails_closed(db):
    t = task(db)
    with kb.write_txn(db):
        db.execute("UPDATE task_events SET kind='not-a-claim' WHERE task_id=? AND kind='claimed'", (t.id,))
    with pytest.raises(ValueError, match="requires independent review"):
        kb.complete_task(db, t.id, metadata=META)


@pytest.mark.parametrize("corruption", ["closed", "foreign", "missing-pointer"])
def test_claim_requires_active_run_on_same_task(db, corruption):
    t = task(db)
    with kb.write_txn(db):
        if corruption == "closed":
            db.execute("UPDATE task_runs SET ended_at=1, status='blocked' WHERE id=?", (t.current_run_id,))
        elif corruption == "foreign":
            db.execute("UPDATE task_runs SET task_id='another-task' WHERE id=?", (t.current_run_id,))
        else:
            db.execute("UPDATE tasks SET current_run_id=NULL WHERE id=?", (t.id,))
    with pytest.raises(ValueError, match="requires independent review"):
        kb.complete_task(db, t.id, metadata=META)
    assert completed_events(db, t.id) == 0


def replace_with_implementation(db, t):
    ok, _ = kb.request_changes(db, t.id, reason="rework", expected_run_id=t.current_run_id)
    assert ok
    replacement = kb.claim_task(db, t.id)
    assert replacement is not None
    assert replacement.current_run_id != t.current_run_id
    return replacement


def test_old_review_claim_does_not_authorize_new_implementation(db):
    t = task(db)
    replacement = replace_with_implementation(db, t)
    with pytest.raises(ValueError, match="requires independent review"):
        kb.complete_task(db, t.id, metadata=META, expected_run_id=replacement.current_run_id)
    assert completed_events(db, t.id) == 0


@pytest.mark.parametrize("expected", [False, True])
def test_requeue_between_preflight_and_write_cannot_complete(db, monkeypatch, expected):
    t = task(db)
    original = kb._merge_completion_prose_artifacts
    def race(*args, **kwargs):
        replace_with_implementation(db, t)
        return original(*args, **kwargs)
    monkeypatch.setattr(kb, "_merge_completion_prose_artifacts", race)
    if expected:
        assert not kb.complete_task(db, t.id, metadata=META, expected_run_id=t.current_run_id)
    else:
        with pytest.raises(ValueError, match="requires independent review"):
            kb.complete_task(db, t.id, metadata=META)
    assert kb.get_task(db, t.id).status == "running"
    assert completed_events(db, t.id) == 0


def test_source_contract_rechecked_under_write_lock(db, monkeypatch):
    t = task(db)
    original = kb._merge_completion_prose_artifacts
    def race(*args, **kwargs):
        changed = {**t.contract, "source_system": "different-ledger"}
        with kb.write_txn(db):
            db.execute("UPDATE tasks SET contract_json=? WHERE id=?", (json.dumps(changed), t.id))
        return original(*args, **kwargs)
    monkeypatch.setattr(kb, "_merge_completion_prose_artifacts", race)
    with pytest.raises(ValueError, match="contract validation failed"):
        kb.complete_task(db, t.id, metadata=META, expected_run_id=t.current_run_id)
    assert completed_events(db, t.id) == 0


def test_provenance_rechecked_under_write_lock(db, monkeypatch):
    t = task(db)
    original = kb._merge_completion_prose_artifacts
    def race(*args, **kwargs):
        with kb.write_txn(db):
            db.execute("UPDATE task_events SET payload='{}' WHERE task_id=? AND kind='claimed'", (t.id,))
        return original(*args, **kwargs)
    monkeypatch.setattr(kb, "_merge_completion_prose_artifacts", race)
    with pytest.raises(ValueError, match="requires independent review"):
        kb.complete_task(db, t.id, metadata=META)
    assert completed_events(db, t.id) == 0


def test_parent_reopened_between_preflight_and_write_blocks(db, monkeypatch):
    parent = kb.create_task(db, title="parent", assignee="builder")
    assert kb.complete_task(db, parent, summary="done")
    t = task(db, parents=[parent])
    original = kb._merge_completion_prose_artifacts
    def race(*args, **kwargs):
        with kb.write_txn(db):
            db.execute("UPDATE tasks SET status='ready' WHERE id=?", (parent,))
        return original(*args, **kwargs)
    monkeypatch.setattr(kb, "_merge_completion_prose_artifacts", race)
    assert not kb.complete_task(db, t.id, metadata=META, expected_run_id=t.current_run_id)
    assert completed_events(db, t.id) == 0
