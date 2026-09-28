"""A worker that finishes cleanly must leave a terminal state behind.

A run that ends its turn without calling ``kanban_complete`` / ``kanban_block``
used to leave the card ``running`` until the claim TTL expired, at which point
the dispatcher reclaimed and re-dispatched the SAME work. Cards 33ad7486 and
3b1350fd exist only because of that, and the re-dispatches around them burned
five runs redoing work already done.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from tools.kanban_tools import finalize_current_worker_from_env as finalize


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    conn = kb.connect()
    try:
        yield conn
    finally:
        conn.close()


def _running_card(conn, monkeypatch, title="Do the thing"):
    task_id = kb.create_task(conn, title=title, assignee="stark")
    run = kb.claim_task(conn, task_id, claimer="stark:test")
    assert run is not None
    kb.heartbeat_claim(conn, task_id, claimer="stark:test")
    conn.execute(
        "UPDATE tasks SET worker_pid = ? WHERE id = ?", (os.getpid(), task_id),
    )
    conn.commit()
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run.current_run_id))
    return task_id, run


def test_clean_finish_without_a_terminal_call_completes_the_card(board, monkeypatch):
    task_id, _ = _running_card(board, monkeypatch)
    assert finalize("I checked the branch and the work had already landed.") is True

    task = kb.get_task(board, task_id)
    assert task is not None
    assert task.status == "done"
    assert "already landed" in (task.result or "")
    # The card must not read as a reviewed, reported completion.
    assert "Auto-finalized" in (task.result or "")


def test_a_failed_turn_is_left_for_the_crash_detector(board, monkeypatch):
    task_id, _ = _running_card(board, monkeypatch)
    assert finalize("partial work", failed=True) is False
    task = kb.get_task(board, task_id)
    assert task is not None
    assert task.status == "running"


def test_a_card_already_completed_by_the_worker_is_untouched(board, monkeypatch):
    task_id, run = _running_card(board, monkeypatch)
    assert kb.complete_task(
        board, task_id, result="reported properly",
        expected_run_id=run.current_run_id,
    )
    assert finalize("stale text") is False
    task = kb.get_task(board, task_id)
    assert task is not None
    assert task.result == "reported properly"


def test_a_card_reclaimed_by_another_worker_is_not_stolen_back(board, monkeypatch):
    task_id, _ = _running_card(board, monkeypatch)
    board.execute(
        "UPDATE tasks SET worker_pid = ? WHERE id = ?", (os.getpid() + 99999, task_id),
    )
    board.commit()
    assert finalize("our stale output") is False
    task = kb.get_task(board, task_id)
    assert task is not None
    assert task.status == "running"


def test_outside_a_dispatcher_worker_it_is_a_noop(board, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    assert finalize("anything") is False


def test_a_turn_that_never_produced_a_result_is_not_auto_completed(board, monkeypatch):
    """cli._finalize_kanban_worker must fail closed when there is no outcome.

    ``_last_turn_result`` stays None when the turn never completed (credential
    failure, agent init failure, an exception on the way in). Reading that as
    "not failed" would close a card whose worker never ran.
    """
    import types
    from hermes_cli.cli_shutdown import _finalize_kanban_worker

    task_id, _ = _running_card(board, monkeypatch, title="Never ran")
    _finalize_kanban_worker(types.SimpleNamespace(_last_turn_result=None))
    task = kb.get_task(board, task_id)
    assert task is not None
    assert task.status == "running"

    # And the positive control: a real structured result does close it.
    _finalize_kanban_worker(types.SimpleNamespace(
        _last_turn_result={"failed": False, "response": "done the work"},
    ))
    task = kb.get_task(board, task_id)
    assert task is not None
    assert task.status == "done"


def test_required_review_auto_finalize_routes_to_contract_reviewer(board, monkeypatch):
    task_id = kb.create_task(
        board, title="Review me", assignee="stark",
        contract={
            "goal_id": "review-auto-finalize",
            "business_domain": "infrastructure",
            "source_system": "test-ledger",
            "deliverable": "verified change",
            "requires_review": True,
            "reviewer_profile": "marshal",
        },
    )
    run = kb.claim_task(board, task_id, claimer="stark:test")
    assert run is not None
    board.execute("UPDATE tasks SET worker_pid=? WHERE id=?", (os.getpid(), task_id))
    board.commit()
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run.current_run_id))

    assert finalize("implementation output") is True
    task = kb.get_task(board, task_id)
    assert task is not None
    assert task.status == "review"
    assert task.assignee == "marshal"
    assert "implementation output" in (kb.latest_run(board, task_id).summary or "")