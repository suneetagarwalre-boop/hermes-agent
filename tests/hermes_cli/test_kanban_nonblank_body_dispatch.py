"""Regression tests for refusing to dispatch underspecified kanban tasks."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture()
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    kb.init_db()
    with kb.connect_closing() as connection:
        yield connection


@pytest.mark.parametrize("body", [None, "", " \t\n"])
def test_ready_task_with_blank_body_is_not_dispatched(conn, body):
    task_id = kb.create_task(
        conn,
        title="underspecified work",
        body=body,
        assignee="worker",
    )
    spawn_calls: list[str] = []

    result = kb.dispatch_once(
        conn,
        spawn_fn=lambda task, _workspace: spawn_calls.append(task.id),
    )

    assert result.spawned == []
    assert result.skipped_blank_body == [task_id]
    assert spawn_calls == []
    task = kb.get_task(conn, task_id)
    assert task is not None
    assert task.status == "ready"


def test_ready_task_with_nonblank_body_still_dispatches(conn):
    task_id = kb.create_task(
        conn,
        title="specified work",
        body="Implement and verify the requested behavior.",
        assignee="worker",
    )

    result = kb.dispatch_once(conn, spawn_fn=lambda _task, _workspace: None)

    assert result.skipped_blank_body == []
    assert [spawned[0] for spawned in result.spawned] == [task_id]


def test_review_task_with_blank_body_is_not_dispatched(conn):
    task_id = kb.create_task(
        conn,
        title="underspecified review",
        body="  ",
        assignee="reviewer",
    )
    conn.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (task_id,))

    result = kb.dispatch_once(conn, spawn_fn=lambda _task, _workspace: None)

    assert result.spawned == []
    assert result.skipped_blank_body == [task_id]


def test_blank_body_is_not_reported_as_spawnable_health_work(conn):
    kb.create_task(conn, title="blank", body="\n\t", assignee="worker")

    assert kb.has_spawnable_ready(conn) is False


def test_dispatch_claim_rechecks_body_atomically(
    conn, monkeypatch: pytest.MonkeyPatch
):
    task_id = kb.create_task(
        conn,
        title="body changed concurrently",
        body="Initially specified.",
        assignee="worker",
    )
    original_claim = kb.claim_task

    def blank_then_claim(connection, claimed_task_id, **kwargs):
        connection.execute(
            "UPDATE tasks SET body = '' WHERE id = ?", (claimed_task_id,)
        )
        return original_claim(connection, claimed_task_id, **kwargs)

    monkeypatch.setattr(kb, "claim_task", blank_then_claim)

    result = kb.dispatch_once(conn, spawn_fn=lambda _task, _workspace: None)

    assert result.spawned == []
    task = kb.get_task(conn, task_id)
    assert task is not None
    assert task.status == "ready"
