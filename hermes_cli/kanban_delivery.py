"""Durable evidence and auditing for Kanban notification delivery.

Cursor claims are leases rather than permanent acknowledgements.  Discord text
receipts are provider-confirmed message IDs; an absent receipt is never treated
as proof of delivery.
"""
from __future__ import annotations

import time


TERMINAL_KINDS = (
    "completed", "blocked", "gave_up", "crashed", "timed_out",
    "review_requested", "block_loop_detected",
)


def migrate(conn) -> None:
    """Create delivery evidence objects after additive columns are present."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS task_delivery_receipts (
            task_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            thread_id TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL,
            confirmed_at INTEGER NOT NULL,
            PRIMARY KEY (task_id, event_id, platform, chat_id, thread_id)
        )"""
    )
    # Historical receipts remain immutable evidence.  A new terminal/reopen
    # event invalidates only the task-level summary flag.
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS reset_task_delivery_on_event
        AFTER INSERT ON task_events WHEN NEW.kind IN
        ('completed','blocked','gave_up','crashed','timed_out',
         'review_requested','block_loop_detected','unblocked')
        BEGIN
          UPDATE tasks SET delivery_confirmed=0, delivery_message_id=NULL,
            delivery_confirmed_at=NULL WHERE id=NEW.task_id;
        END"""
    )


def receipt(conn, task_id, event_id, platform, chat_id, thread_id=""):
    return conn.execute(
        """SELECT message_id FROM task_delivery_receipts
        WHERE task_id=? AND event_id=? AND platform=? AND chat_id=? AND thread_id=?""",
        (task_id, int(event_id), platform, str(chat_id), thread_id or ""),
    ).fetchone()


def confirm(conn, *, task_id, event_id, platform, chat_id, message_id, thread_id="") -> None:
    """Persist a provider-returned Discord message ID for the exact event."""
    if platform != "discord" or not str(message_id or "").isdigit():
        raise ValueError("Discord confirmation requires a provider message ID")
    event = conn.execute(
        "SELECT task_id FROM task_events WHERE id=?", (int(event_id),)
    ).fetchone()
    if not event or event[0] != task_id:
        raise ValueError("receipt event does not belong to task")
    now = int(time.time())
    conn.execute(
        """INSERT INTO task_delivery_receipts
        (task_id,event_id,platform,chat_id,thread_id,message_id,confirmed_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(task_id,event_id,platform,chat_id,thread_id)
        DO UPDATE SET message_id=excluded.message_id,
                      confirmed_at=excluded.confirmed_at""",
        (task_id, int(event_id), platform, str(chat_id), thread_id or "", str(message_id), now),
    )
    latest = conn.execute(
        "SELECT MAX(id) FROM task_events WHERE task_id=? AND kind IN "
        "('completed','blocked','gave_up','crashed','timed_out',"
        "'review_requested','block_loop_detected','unblocked')",
        (task_id,),
    ).fetchone()[0]
    if latest == int(event_id):
        conn.execute(
            "UPDATE tasks SET delivery_confirmed=1, delivery_message_id=?, "
            "delivery_confirmed_at=? WHERE id=?",
            (str(message_id), now, task_id),
        )


def undelivered_terminal(conn, *, limit=100, routable_only=False):
    """Return terminal cards lacking provider-confirmed Discord text delivery.

    With ``routable_only``, include only cards with a Discord subscription whose
    mode includes a passive text send.  Wake-only subscriptions have no direct
    provider message ID and therefore cannot be audited by this receipt ledger.
    """
    routable = ""
    if routable_only:
        routable = (
            "AND EXISTS (SELECT 1 FROM kanban_notify_subs s "
            "WHERE s.task_id=t.id AND lower(s.platform)='discord' "
            "AND COALESCE(s.delivery_mode,'notify') != 'wake')"
        )
    return [
        dict(row)
        for row in conn.execute(
            f"""SELECT t.id,t.status,t.completed_at,
            EXISTS(SELECT 1 FROM kanban_notify_subs s WHERE s.task_id=t.id
                   AND lower(s.platform)='discord'
                   AND COALESCE(s.delivery_mode,'notify') != 'wake') AS has_subscription
            FROM tasks t
            WHERE t.status IN ('done','blocked','review','triage')
              AND t.delivery_confirmed=0 {routable}
            ORDER BY t.created_at DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    ]
