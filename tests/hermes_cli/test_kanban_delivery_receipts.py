import time
import pytest
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_delivery as delivery


@pytest.fixture
def conn(tmp_path):
    c = kb.connect(tmp_path / 'board.db')
    yield c
    c.close()


def completed(conn, **kwargs):
    tid = kb.create_task(conn, title='Synthetic receipt test', assignee='stark')
    kb.promote_task(conn, tid, actor='test')
    assert kb.complete_task(conn, tid, **kwargs)
    event = conn.execute("SELECT MAX(id) FROM task_events WHERE task_id=? AND kind='completed'", (tid,)).fetchone()[0]
    return tid, event


def test_summary_only_is_canonical_result(conn):
    tid, _ = completed(conn, summary='The actual answer')
    assert kb.get_task(conn, tid).result == 'The actual answer'


def test_explicit_full_result_is_preserved(conn):
    tid, _ = completed(conn, summary='short', result='full\nanswer')
    assert kb.get_task(conn, tid).result == 'full\nanswer'


def test_terminal_without_route_is_surfaced_not_confirmed(conn):
    tid, _ = completed(conn, summary='answer')
    assert any(r['id'] == tid and not r['has_subscription'] for r in delivery.undelivered_terminal(conn))


def test_receipt_requires_message_and_correct_event(conn):
    tid, event = completed(conn, summary='answer')
    for message_id, eid in [(None,event), ('123',event+100)]:
        with pytest.raises(ValueError):
            with kb.write_txn(conn):
                delivery.confirm(conn, task_id=tid, event_id=eid, platform='discord', chat_id='12', message_id=message_id)
    assert conn.execute('SELECT delivery_confirmed FROM tasks WHERE id=?',(tid,)).fetchone()[0] == 0


def test_real_receipt_is_idempotent_and_reopen_resets_flag(conn):
    tid,event = completed(conn, summary='answer')
    for _ in range(2):
        with kb.write_txn(conn):
            delivery.confirm(conn, task_id=tid,event_id=event,platform='discord',chat_id='12',message_id='123456789')
    assert conn.execute('SELECT delivery_confirmed FROM tasks WHERE id=?',(tid,)).fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM task_delivery_receipts').fetchone()[0] == 1
    with kb.write_txn(conn):
        kb._append_event(conn, tid, 'unblocked', {})
    assert conn.execute('SELECT delivery_confirmed FROM tasks WHERE id=?',(tid,)).fetchone()[0] == 0
    assert delivery.receipt(conn,tid,event,'discord','12')


def test_lease_recovers_death_after_cursor_claim(conn):
    tid,event=completed(conn,summary='answer')
    kb.add_notify_sub(conn,task_id=tid,platform='discord',chat_id='12')
    # Subscription defaults may start at current event; explicitly subscribe from zero.
    with kb.write_txn(conn):
        conn.execute('UPDATE kanban_notify_subs SET last_event_id=0 WHERE task_id=?',(tid,))
    args=dict(task_id=tid,platform='discord',chat_id='12',kinds=['completed'])
    old,new,events=kb.claim_unseen_events_for_sub(conn,**args)
    assert [e.id for e in events]==[event]
    assert not kb.claim_unseen_events_for_sub(conn,**args)[2]
    assert conn.execute('SELECT delivery_confirmed FROM tasks WHERE id=?',(tid,)).fetchone()[0]==0
    with kb.write_txn(conn):
        conn.execute('UPDATE kanban_notify_subs SET lease_until=? WHERE task_id=?',(int(time.time())-1,tid))
    assert [e.id for e in kb.claim_unseen_events_for_sub(conn,**args)[2]]==[event]
    kb.advance_notify_cursor(conn,task_id=tid,platform='discord',chat_id='12',new_cursor=new)
    assert not kb.claim_unseen_events_for_sub(conn,**args)[2]


def test_rewind_releases_lease_for_retry(conn):
    tid,event=completed(conn,summary='answer')
    kb.add_notify_sub(conn,task_id=tid,platform='discord',chat_id='12')
    with kb.write_txn(conn):
        conn.execute('UPDATE kanban_notify_subs SET last_event_id=0 WHERE task_id=?',(tid,))
    args=dict(task_id=tid,platform='discord',chat_id='12')
    old,new,events=kb.claim_unseen_events_for_sub(conn,**args,kinds=['completed'])
    assert kb.rewind_notify_cursor(conn,**args,claimed_cursor=new,old_cursor=old)
    assert kb.claim_unseen_events_for_sub(conn,**args,kinds=['completed'])[2]
