import time

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_delivery as delivery
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn


@pytest.fixture
def conn(tmp_path):
    connection = kbc.connect(tmp_path / "board.db")
    yield connection
    connection.close()


def completed(conn, **kwargs):
    task_id = kb.create_task(conn, title="Synthetic receipt test", assignee="worker")
    assert kb.complete_task(conn, task_id, **kwargs)
    event_id = conn.execute(
        "SELECT MAX(id) FROM task_events WHERE task_id=? AND kind='completed'",
        (task_id,),
    ).fetchone()[0]
    return task_id, event_id


def test_summary_only_is_canonical_result(conn):
    task_id, _ = completed(conn, summary="The actual answer")
    assert kb.get_task(conn, task_id).result == "The actual answer"


def test_receipt_requires_provider_message_and_matching_event(conn):
    task_id, event_id = completed(conn, summary="answer")
    for message_id, candidate in ((None, event_id), ("123", event_id + 100)):
        with pytest.raises(ValueError):
            with kb.write_txn(conn):
                delivery.confirm(
                    conn, task_id=task_id, event_id=candidate,
                    platform="discord", chat_id="12", message_id=message_id,
                )
    assert not kb.get_task(conn, task_id).delivery_confirmed


def test_receipt_is_idempotent_and_new_terminal_event_resets_summary(conn):
    task_id, event_id = completed(conn, summary="answer")
    for _ in range(2):
        with kb.write_txn(conn):
            delivery.confirm(
                conn, task_id=task_id, event_id=event_id,
                platform="discord", chat_id="12", message_id="123456789",
            )
    assert kb.get_task(conn, task_id).delivery_confirmed
    assert conn.execute("SELECT COUNT(*) FROM task_delivery_receipts").fetchone()[0] == 1
    with kb.write_txn(conn):
        kb._append_event(conn, task_id, "unblocked", {})
    assert not kb.get_task(conn, task_id).delivery_confirmed
    assert delivery.receipt(conn, task_id, event_id, "discord", "12")


def test_routable_sweeper_only_includes_confirmable_discord_text_routes(conn):
    unroutable, _ = completed(conn, summary="unroutable")
    wake_only, _ = completed(conn, summary="wake")
    routable, _ = completed(conn, summary="text")
    kbn.add_notify_sub(
        conn, task_id=wake_only, platform="discord", chat_id="41", delivery_mode="wake"
    )
    kbn.add_notify_sub(
        conn, task_id=routable, platform="discord", chat_id="42", delivery_mode="notify"
    )
    rows = delivery.undelivered_terminal(conn, routable_only=True)
    ids = {row["id"] for row in rows}
    assert routable in ids
    assert wake_only not in ids
    assert unroutable not in ids
    assert all(row["has_subscription"] for row in rows)


def test_lease_recovers_process_death_after_cursor_claim(conn):
    task_id, event_id = completed(conn, summary="answer")
    kbn.add_notify_sub(conn, task_id=task_id, platform="discord", chat_id="12")
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE kanban_notify_subs SET last_event_id=0 WHERE task_id=?", (task_id,)
        )
    args = dict(
        task_id=task_id, platform="discord", chat_id="12", kinds=["completed"]
    )
    old, new, events = kbn.claim_unseen_events_for_sub(conn, **args)
    assert [event.id for event in events] == [event_id]
    assert kbn.claim_unseen_events_for_sub(conn, **args)[2] == []
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE kanban_notify_subs SET lease_until=? WHERE task_id=?",
            (int(time.time()) - 1, task_id),
        )
    assert [event.id for event in kbn.claim_unseen_events_for_sub(conn, **args)[2]] == [event_id]
    kbn.advance_notify_cursor(conn, task_id=task_id, platform="discord", chat_id="12", new_cursor=new)
    assert kbn.claim_unseen_events_for_sub(conn, **args)[2] == []
    row = conn.execute(
        "SELECT lease_until FROM kanban_notify_subs WHERE task_id=?", (task_id,)
    ).fetchone()
    assert row["lease_until"] == 0


def test_rewind_releases_lease_for_immediate_retry(conn):
    task_id, _ = completed(conn, summary="answer")
    kbn.add_notify_sub(conn, task_id=task_id, platform="discord", chat_id="12")
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE kanban_notify_subs SET last_event_id=0 WHERE task_id=?", (task_id,)
        )
    args = dict(task_id=task_id, platform="discord", chat_id="12")
    old, new, events = kbn.claim_unseen_events_for_sub(conn, **args, kinds=["completed"])
    assert events
    assert kbn.rewind_notify_cursor(conn, **args, claimed_cursor=new, old_cursor=old)
    assert kbn.claim_unseen_events_for_sub(conn, **args, kinds=["completed"])[2]
