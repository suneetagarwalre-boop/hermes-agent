"""Regression tests for ``worker_exit_excerpt``.

A worker that exits rc=0 without calling ``kanban_complete`` /
``kanban_block`` used to be reported with a fixed sentence and an empty
``full_output``. That made two opposite situations indistinguishable:
a worker that did the work and skipped the paperwork, versus a worker
that died on a startup error having done nothing at all. On 2026-08-18
the second kind (a SyntaxError from an unresolved merge conflict left in
``agent/conversation_loop.py``) took the whole worker fleet down for
three hours while every alert said only "protocol violation".
"""

import pytest

from hermes_cli import kanban_db


REAL_CRASH_LOG = """Warning: Unknown toolsets: messaging
Query: work kanban task fbbbe3f2
Initializing agent...
────────────

 ─  ⚕ Hermes  ──────────────────────────────────────

 Error: invalid decimal literal (conversation_loop.py, line 8589)

 ────────────────────────────────────────

Goodbye! ⚕
"""


def _patch_log(monkeypatch, text):
    monkeypatch.setattr(
        kanban_db, "read_worker_log", lambda task_id, **kw: text
    )


def test_excerpt_surfaces_the_startup_error(monkeypatch):
    """The one line that explains the failure must survive into the excerpt."""
    _patch_log(monkeypatch, REAL_CRASH_LOG)
    excerpt = kanban_db.worker_exit_excerpt("t1")
    assert excerpt is not None
    assert "invalid decimal literal" in excerpt
    assert "conversation_loop.py, line 8589" in excerpt


def test_excerpt_strips_box_drawing_frames(monkeypatch):
    """TUI banner rules carry no signal and must not eat the line budget."""
    _patch_log(monkeypatch, REAL_CRASH_LOG)
    excerpt = kanban_db.worker_exit_excerpt("t1")
    assert "─" not in excerpt
    assert "│" not in excerpt


def test_excerpt_collapses_repeated_lines(monkeypatch):
    """A crash-loop repeats one banner; the excerpt should show it once."""
    _patch_log(monkeypatch, "boom\n" * 50 + "final line\n")
    excerpt = kanban_db.worker_exit_excerpt("t1")
    assert excerpt == "boom | final line"


def test_excerpt_is_bounded(monkeypatch):
    """Excerpts are appended to an error column — they must stay small."""
    _patch_log(monkeypatch, "\n".join(f"line-{i}" for i in range(500)))
    excerpt = kanban_db.worker_exit_excerpt("t1")
    assert excerpt is not None
    assert len(excerpt) <= 500
    # Keeps the END of the log, which is where the failure is.
    assert "line-499" in excerpt


def test_missing_log_returns_none(monkeypatch):
    """No log (task never spawned, or GC'd) is not an error."""
    _patch_log(monkeypatch, None)
    assert kanban_db.worker_exit_excerpt("t1") is None


def test_blank_log_returns_none(monkeypatch):
    _patch_log(monkeypatch, "   \n\n ─── \n")
    assert kanban_db.worker_exit_excerpt("t1") is None


def test_unreadable_log_does_not_raise(monkeypatch):
    """The reap loop must never die because a log was unreadable."""
    def _boom(task_id, **kw):
        raise OSError("disk gone")
    monkeypatch.setattr(kanban_db, "read_worker_log", _boom)
    assert kanban_db.worker_exit_excerpt("t1") is None


def test_excerpt_is_redacted_and_control_safe(monkeypatch):
    secret = "sk-test_abcdefghijklmnopqrstuvwxyz123456"
    _patch_log(
        monkeypatch,
        f"\x1b]0;spoofed title\x07\nAuthorization: Bearer {secret}\x00\n"
        "Error: startup failed\n",
    )
    excerpt = kanban_db.worker_exit_excerpt("t1")
    assert excerpt is not None
    assert secret not in excerpt
    assert "\x1b" not in excerpt
    assert "\x00" not in excerpt
    assert "startup failed" in excerpt


def test_excerpt_passes_explicit_board_to_log_reader(monkeypatch):
    seen = []

    def _read(task_id, **kwargs):
        seen.append((task_id, kwargs))
        return "Error: board-local startup failure"

    monkeypatch.setattr(kanban_db, "read_worker_log", _read)
    excerpt = kanban_db.worker_exit_excerpt("same-id", board="named-board")
    assert excerpt is not None
    assert "board-local startup failure" in excerpt
    assert seen == [("same-id", {"tail_bytes": 8192, "board": "named-board"})]


def test_reaper_keeps_diagnostic_out_of_retry_prompt_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "reaper.db"))
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    kanban_db.init_db()
    monkeypatch.setattr(kanban_db, "_pid_alive", lambda _pid: False)
    seen_boards = []

    def _excerpt(task_id, *, board=None, **_kwargs):
        seen_boards.append(board)
        return "Error: safe startup diagnostic"

    monkeypatch.setattr(kanban_db, "worker_exit_excerpt", _excerpt)
    with kanban_db.connect() as conn:
        tid = kanban_db.create_task(conn, title="rc0", assignee="worker")
        host = kanban_db._claimer_id().split(":", 1)[0]
        claimed = kanban_db.claim_task(conn, tid, claimer=f"{host}:test")
        assert claimed is not None
        pid = 991234
        kanban_db._set_worker_pid(conn, tid, pid)
        kanban_db._record_worker_exit(pid, 0)

        assert tid in kanban_db.detect_crashed_workers(conn, board="named-board")
        run = conn.execute(
            "SELECT error FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (tid,),
        ).fetchone()
        assert run is not None
        event = [
            e for e in kanban_db.list_events(conn, tid)
            if e.kind == "protocol_violation"
        ][-1]

    assert seen_boards == ["named-board"]
    assert "safe startup diagnostic" not in run["error"]
    assert event.payload["worker_log_excerpt"] == "Error: safe startup diagnostic"
