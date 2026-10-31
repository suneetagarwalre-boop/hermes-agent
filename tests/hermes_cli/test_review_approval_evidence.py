"""Contracted QC cannot approve a partial report or empty reviewer receipt."""
from pathlib import Path
import uuid

import pytest
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc

SCOPE = {"business_domain": "infrastructure", "source_system": "test-ledger"}
APPROVAL = {**SCOPE, "review_outcome": "approved", "reviewer_checks": ["Executed the delivered artifact and checked the requested behavior"]}

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_HOME", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(kb, "_fire_kanban_lifecycle_hook", lambda *a, **k: None)
    monkeypatch.setattr(kb, "_cleanup_workspace", lambda *a, **k: None)
    kb.init_db()
    with kbc.connect_closing() as conn:
        yield conn


def review_task(db):
    tid = kb.create_task(db, title="A working reviewed deliverable", assignee="builder", contract={**SCOPE,"goal_id":str(uuid.uuid4()), "deliverable":"tested artifact", "requires_review":True})
    assert kb.request_review(db, tid, summary="Candidate ready", reviewer="marshal")
    t = kb.claim_review_task(db, tid)
    assert t is not None
    return t


@pytest.mark.parametrize("metadata", [SCOPE, {**SCOPE,"review_outcome":"partial"}, {**SCOPE,"review_outcome":"approved"}, {**APPROVAL,"reviewer_checks":[]}, {**APPROVAL,"reviewer_checks":[""]}, {**APPROVAL,"overall_completion_approved":False}])
def test_bad_receipt_cannot_complete_reviewed_business_goal(db, metadata):
    t=review_task(db)
    with pytest.raises(ValueError, match="review approval evidence"):
        kb.complete_task(db,t.id,summary="Partial only, not a finished deliverable",metadata=metadata,expected_run_id=t.current_run_id)
    assert kb.get_task(db,t.id).status == "running"


def test_concrete_approval_can_complete(db):
    t=review_task(db)
    assert kb.complete_task(db,t.id,summary="Approved after independent execution",metadata=APPROVAL,expected_run_id=t.current_run_id)
    assert kb.get_task(db,t.id).status == "done"


def test_reviewer_cannot_auto_finalize_without_a_verdict(db,monkeypatch):
    from tools import kanban_tools
    t=review_task(db)
    monkeypatch.setenv("HERMES_KANBAN_TASK",t.id)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID",str(t.current_run_id))
    assert kanban_tools.finalize_current_worker_from_env("Looks fine") is False
    assert kb.get_task(db,t.id).status == "running"
