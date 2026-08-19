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
    assert len(excerpt) <= 700
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
