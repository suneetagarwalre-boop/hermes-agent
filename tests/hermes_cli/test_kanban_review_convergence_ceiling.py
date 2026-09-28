"""A rebuild/reject pair must not be able to trade a card forever.

``request_changes`` neither increments nor resets ``consecutive_failures``, so
the circuit breaker never observes a review loop. Card ``t_b66565e8`` exploited
exactly that: 23 runs across 47 minutes (bernie rebuilding, katt rejecting),
converging on nothing, archived having delivered nothing. Past
``_REVIEW_ROUND_TRIP_LIMIT`` round trips the card stops going back to the
implementer and parks as ``blocked`` so a human makes the call the loop was
substituting for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def conn(tmp_path: Path):
    db = kb.connect(tmp_path / "kanban.db")
    try:
        yield db
    finally:
        db.close()


def _one_round_trip(conn, task_id: str, n: int):
    """implementer claims -> requests review -> reviewer claims -> rejects."""
    implementation = kb.claim_task(conn, task_id, claimer=f"builder:{n}")
    assert implementation is not None
    assert kb.request_review(
        conn, task_id, reviewer="reviewer",
        summary=f"rebuild {n}",
        expected_run_id=implementation.current_run_id,
    )
    review = kb.claim_review_task(conn, task_id, claimer=f"reviewer:{n}")
    assert review is not None
    return kb.request_changes(
        conn, task_id,
        reason=f"still not actionable ({n})",
        expected_run_id=review.current_run_id,
    )


def _events(conn, task_id, kind):
    return [e for e in kb.list_events(conn, task_id) if e.kind == kind]


def test_review_loop_parks_as_blocked_at_the_ceiling(conn):
    task_id = kb.create_task(conn, title="Triage Personal HQ", assignee="builder")

    # Every round trip below the ceiling routes back to the implementer.
    for n in range(1, kb._REVIEW_ROUND_TRIP_LIMIT):
        assert _one_round_trip(conn, task_id, n) == (True, "builder")
        task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "ready", f"round trip {n} should still rework"
        assert task.assignee == "builder"

    # The one that reaches the ceiling parks instead of re-dispatching.
    assert _one_round_trip(conn, task_id, kb._REVIEW_ROUND_TRIP_LIMIT) == (True, "builder")
    task = kb.get_task(conn, task_id)
    assert task is not None
    assert task.status == "blocked"
    assert task.current_run_id is None

    stalled = _events(conn, task_id, "review_not_converging")
    assert len(stalled) == 1
    payload = stalled[-1].payload
    assert payload["round_trips"] == kb._REVIEW_ROUND_TRIP_LIMIT
    assert payload["limit"] == kb._REVIEW_ROUND_TRIP_LIMIT
    assert payload["implementer"] == "builder"
    assert "did not converge" in payload["reason"]
    # The reviewer's actual objection has to survive into the parked reason,
    # otherwise a human is asked to adjudicate with no evidence.
    assert "still not actionable" in payload["reason"]


def test_a_converging_review_never_trips_the_ceiling(conn):
    """Approval before the ceiling must still complete normally."""
    task_id = kb.create_task(conn, title="Ships on review 2", assignee="builder")
    assert _one_round_trip(conn, task_id, 1) == (True, "builder")

    implementation = kb.claim_task(conn, task_id, claimer="builder:2")
    assert implementation is not None
    assert kb.request_review(
        conn, task_id, reviewer="reviewer", summary="fixed",
        expected_run_id=implementation.current_run_id,
    )
    review = kb.claim_review_task(conn, task_id, claimer="reviewer:2")
    assert review is not None
    assert kb.complete_task(
        conn, task_id, result="approved", expected_run_id=review.current_run_id,
    )

    task = kb.get_task(conn, task_id)
    assert task is not None
    assert task.status == "done"
    assert _events(conn, task_id, "review_not_converging") == []


def test_parking_emits_a_blocked_event_the_notifier_can_see(conn):
    """The gateway notifier keys off TERMINAL_KINDS, which includes 'blocked'.

    Without this event the card parks silently and the loop is merely slower,
    not surfaced.
    """
    task_id = kb.create_task(conn, title="Silent park regression", assignee="builder")
    for n in range(1, kb._REVIEW_ROUND_TRIP_LIMIT + 1):
        assert _one_round_trip(conn, task_id, n) == (True, "builder")

    blocked = _events(conn, task_id, "blocked")
    assert len(blocked) == 1
    payload = blocked[-1].payload
    assert payload["kind"] == "review_not_converging"
    assert payload["recurrences"] == kb._REVIEW_ROUND_TRIP_LIMIT
    assert "did not converge" in payload["reason"]
    assert "still not actionable" in payload["reason"]