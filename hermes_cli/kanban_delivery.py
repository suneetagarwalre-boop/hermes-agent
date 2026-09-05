"""Durable Discord delivery evidence, independent of task completion.

Receipts are at-least-once: a process can die after Discord accepts a message
and before SQLite commits its ID. Missing evidence must never mean delivered.
"""
import time

from hermes_cli.sqlite_util import add_column_if_missing

TERMINAL_KINDS = ('completed', 'blocked', 'gave_up', 'crashed', 'timed_out', 'review_requested', 'block_loop_detected')


def migrate(conn):
    for name, declaration in (
        ('delivery_confirmed', 'INTEGER NOT NULL DEFAULT 0'),
        ('delivery_message_id', 'TEXT'),
        ('delivery_confirmed_at', 'INTEGER'),
    ):
        add_column_if_missing(conn, 'tasks', name, f'{name} {declaration}')
    if conn.execute("PRAGMA table_info(kanban_notify_subs)").fetchone():
        for name in ('lease_old_cursor', 'lease_until'):
            add_column_if_missing(conn, 'kanban_notify_subs', name, f'{name} INTEGER NOT NULL DEFAULT 0')
    conn.execute('''CREATE TABLE IF NOT EXISTS task_delivery_receipts (
        task_id TEXT NOT NULL, event_id INTEGER NOT NULL,
        platform TEXT NOT NULL, chat_id TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '',
        message_id TEXT NOT NULL, confirmed_at INTEGER NOT NULL,
        PRIMARY KEY(task_id,event_id,platform,chat_id,thread_id)
    )''')
    # Reopening or a new terminal event invalidates the summary flag, never
    # the historical per-event proof. Old binaries also execute this trigger.
    conn.execute('''CREATE TRIGGER IF NOT EXISTS reset_task_delivery_on_event
        AFTER INSERT ON task_events WHEN NEW.kind IN
        ('completed','blocked','gave_up','crashed','timed_out','review_requested','block_loop_detected','unblocked')
        BEGIN UPDATE tasks SET delivery_confirmed=0, delivery_message_id=NULL,
          delivery_confirmed_at=NULL WHERE id=NEW.task_id; END''')


def receipt(conn, task_id, event_id, platform, chat_id, thread_id=''):
    return conn.execute('''SELECT message_id FROM task_delivery_receipts
        WHERE task_id=? AND event_id=? AND platform=? AND chat_id=? AND thread_id=?''',
        (task_id, event_id, platform, str(chat_id), thread_id or '')).fetchone()


def confirm(conn, *, task_id, event_id, platform, chat_id, message_id, thread_id=''):
    """Persist only a provider-returned message ID, not a successful cursor move."""
    if platform != 'discord' or not str(message_id or '').isdigit():
        raise ValueError('Discord confirmation requires a provider message ID')
    event = conn.execute('SELECT task_id FROM task_events WHERE id=?', (event_id,)).fetchone()
    if not event or event[0] != task_id:
        raise ValueError('Receipt event does not belong to task')
    now = int(time.time())
    conn.execute('''INSERT INTO task_delivery_receipts
        (task_id,event_id,platform,chat_id,thread_id,message_id,confirmed_at)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(task_id,event_id,platform,chat_id,thread_id)
        DO UPDATE SET message_id=excluded.message_id,confirmed_at=excluded.confirmed_at''',
        (task_id,event_id,platform,str(chat_id),thread_id or '',str(message_id),now))
    latest = conn.execute("SELECT MAX(id) FROM task_events WHERE task_id=? AND kind IN ('completed','blocked','gave_up','crashed','timed_out','review_requested','block_loop_detected','unblocked')", (task_id,)).fetchone()[0]
    if latest == event_id:
        conn.execute('UPDATE tasks SET delivery_confirmed=1,delivery_message_id=?,delivery_confirmed_at=? WHERE id=?', (str(message_id),now,task_id))


def undelivered_terminal(conn, *, limit=100):
    """Read-only sweeper output: includes unroutable cards, never invents a target."""
    return [dict(row) for row in conn.execute('''SELECT t.id,t.status,t.completed_at,
        EXISTS(SELECT 1 FROM kanban_notify_subs s WHERE s.task_id=t.id) AS has_subscription
        FROM tasks t WHERE t.status IN ('done','blocked','review','triage')
        AND t.delivery_confirmed=0 ORDER BY t.created_at DESC LIMIT ?''', (limit,)).fetchall()]
