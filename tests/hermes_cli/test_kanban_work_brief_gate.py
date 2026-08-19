"""Regression coverage through the real Kanban create and dispatch paths."""

from __future__ import annotations

import io
import shlex
from pathlib import Path

import pytest

from hermes_cli import kanban as kanban_cli
from hermes_cli import kanban_db as kb


VALID_CONTENT_BRIEF = """\
Action: Draft the launch email from approved copy.
Source: Google Doc 1AbC-launch-copy, section Final positioning.
Scope: Include subject, preview, and body; exclude SMS and landing-page copy.
Acceptance: Return one complete draft grounded only in the named section.
If absent: Stop and report that the named Google Doc or section is missing.
"""

VALID_INFRA_BRIEF = """\
Action: Update the Hermes gateway health probe.
Source: Repository NousResearch/hermes-agent, gateway/status.py.
Scope: Include the probe and regression tests; exclude routing and alert redesign.
Acceptance: Run the focused pytest file and show all tests passing.
If absent: Stop and report the missing repository path; do not invent a replacement.
"""


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give create/dispatch integration tests an isolated Kanban database."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()
    return home


def _create_command(title: str, body: str, *, assignee: str = "stark") -> str:
    return " ".join(
        [
            "create",
            shlex.quote(title),
            "--assignee",
            shlex.quote(assignee),
            "--body",
            shlex.quote(body),
        ]
    )


def _tasks():
    with kb.connect_closing() as conn:
        return kb.list_tasks(conn, limit=100)


def test_heredoc_body_dash_is_read_and_created_verbatim(
    kanban_home, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("sys.stdin", io.StringIO(VALID_INFRA_BRIEF))

    output = kanban_cli.run_slash(
        "create 'infra heredoc brief' --assignee stark --body -"
    )

    assert output.startswith("Created t_")
    tasks = _tasks()
    assert len(tasks) == 1
    assert tasks[0].body == VALID_INFRA_BRIEF


def test_title_only_card_is_rejected_before_db_write(kanban_home):
    output = kanban_cli.run_slash("create 'title only' --assignee stark")

    assert "work brief is <missing>" in output
    assert _tasks() == []


def test_unassigned_action_card_still_requires_structured_brief(kanban_home):
    output = kanban_cli.run_slash(
        "create 'unassigned action' --body 'Investigate this request.'"
    )

    assert "The work brief is incomplete" in output
    assert "source/system and exact identifier when known" in output
    assert _tasks() == []


@pytest.mark.parametrize("body", ["-", "tbd", "placeholder", "   "])
def test_placeholder_body_is_rejected_before_db_write(
    kanban_home, monkeypatch: pytest.MonkeyPatch, body: str
):
    # A literal '-' is stdin syntax, so make the source deterministically empty.
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    output = kanban_cli.run_slash(_create_command("placeholder brief", body))

    assert "work brief" in output
    assert _tasks() == []


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("draft launch email", VALID_CONTENT_BRIEF),
        ("patch gateway health probe", VALID_INFRA_BRIEF),
    ],
)
def test_valid_content_and_infra_briefs_create_through_real_cli(
    kanban_home, title: str, body: str
):
    output = kanban_cli.run_slash(_create_command(title, body))

    assert output.startswith("Created t_")
    assert [task.title for task in _tasks()] == [title]


def test_simple_status_query_stays_no_card_work(kanban_home):
    output = kanban_cli.run_slash(
        "create \"What's the status of task t_44ad6ec3?\" --assignee stark"
    )

    assert "Answer it directly" in output
    assert "do not create a Kanban card" in output
    assert _tasks() == []


def test_real_dispatch_rejects_legacy_incomplete_brief(
    kanban_home, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    with kb.connect_closing() as conn:
        task_id = kb.create_task(
            conn,
            title="legacy incomplete card",
            body="Action: Patch it.\nSource: repo/example.",
            assignee="stark",
        )
        spawn_calls: list[str] = []
        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda task, _workspace: spawn_calls.append(task.id),
        )
        task = kb.get_task(conn, task_id)

    assert result.spawned == []
    assert spawn_calls == []
    assert task is not None and task.status == "ready"
    assert result.rejected_incomplete_brief[0][0] == task_id
    error = result.rejected_incomplete_brief[0][1]
    assert "scope, including inclusions and exclusions" in error
    assert "acceptance check" in error
    assert "what to do if the source is absent" in error


def test_real_create_then_dispatch_spawns_valid_brief(
    kanban_home, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    output = kanban_cli.run_slash(
        _create_command("dispatch valid infra brief", VALID_INFRA_BRIEF)
    )
    assert output.startswith("Created t_")

    spawned_ids: list[str] = []
    with kb.connect_closing() as conn:
        task_id = kb.list_tasks(conn, limit=1)[0].id
        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda task, _workspace: spawned_ids.append(task.id),
        )

    assert result.rejected_incomplete_brief == []
    assert spawned_ids == [task_id]
    assert [row[0] for row in result.spawned] == [task_id]


def test_claim_time_recheck_closes_concurrent_brief_edit_race(
    kanban_home, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    with kb.connect_closing() as conn:
        task_id = kb.create_task(
            conn,
            title="racy brief",
            body=VALID_INFRA_BRIEF,
            assignee="stark",
        )
        original_claim = kb.claim_task

        def invalidate_then_claim(connection, claimed_task_id, **kwargs):
            connection.execute(
                "UPDATE tasks SET body = '-' WHERE id = ?", (claimed_task_id,)
            )
            return original_claim(connection, claimed_task_id, **kwargs)

        monkeypatch.setattr(kb, "claim_task", invalidate_then_claim)
        result = kb.dispatch_once(
            conn, spawn_fn=lambda _task, _workspace: pytest.fail("must not spawn")
        )
        task = kb.get_task(conn, task_id)

    assert result.spawned == []
    assert task is not None and task.status == "ready"
