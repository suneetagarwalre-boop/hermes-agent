"""Regressions for the upstream merge port (review t_b167e1da).

Ledgered finals keep delivery_id / notify / source_message_id through boot replay, on the
normal and the uncertain-streamed path alike; terminal refusals are never retried; Kanban
request origin is the bound actor and fails closed on a joined multi-sender turn.
Adapted from Marshal's reviewer probes, with the defect assertions inverted.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway import delivery_ledger as dl
from gateway.config import Platform
from gateway.run_startup import GatewayStartupMixin
from tests.gateway.test_delivery_ledger_producer import _Adapter, _event
from tests.gateway.test_discord_recipient_producers import adapter  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, '_db_path', lambda: tmp_path / 'review.db')


class Runner(GatewayStartupMixin):
    def __init__(self, adapter):
        self._obligation_adapter = AsyncMock(return_value=adapter)
        self._arm_flood_timers_for_waiting_rows = AsyncMock()


@pytest.mark.asyncio
async def test_normal_metadata_survives_boot_claim_and_replay(monkeypatch):
    from gateway.platforms.base import SendResult
    adapter = _Adapter()
    event = _event(); event.source.platform = Platform.DISCORD
    content = '[[discord-recipients:["Jeff"]]]\nReport ready'

    async def crash(**kwargs):
        raise RuntimeError('simulated crash before ACK')
    adapter._send_with_retry = crash
    with pytest.raises(RuntimeError, match='simulated crash'):
        await adapter.send_final_ledgered(event, 'review', content, {'route': 'preserved'}, reply_to='msg-42')
    oid = dl.compute_obligation_id('review', 'msg-42', content)
    assert dl.get_delivery_metadata(oid) == {'route': 'preserved', 'delivery_id': oid, 'notify': True,
                                             'source_message_id': 'msg-42'}
    monkeypatch.setattr(dl, '_owner_alive', lambda *args: False)
    rows = dl.sweep_recoverable()
    assert len(rows) == 1 and rows[0]['needs_marker']
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id='recovered'))
    assert await Runner(adapter)._redeliver_claimed_obligations(rows) == 1
    kwargs = adapter.send.await_args.kwargs
    assert kwargs['reply_to'] == 'msg-42'
    assert kwargs['metadata']['delivery_id'] == oid
    assert kwargs['metadata']['notify'] is True
    assert kwargs['metadata']['route'] == 'preserved'


@pytest.mark.asyncio
async def test_terminal_refusal_never_claimed_or_scheduled(monkeypatch):
    from gateway.platforms.base import SendResult
    adapter = _Adapter(); event = _event(); event.source.platform = Platform.DISCORD
    adapter.gateway_runner = MagicMock()
    adapter.gateway_runner._delivery_adapter_for.return_value = adapter
    adapter._send_with_retry = AsyncMock(return_value=SendResult(
        success=False, error='discord_recipient_contract_refused:missing'))
    adapter._send_retry_is_final = lambda result: True
    await adapter.send_final_ledgered(event, 'review', 'answer', {}, reply_to='msg-42')
    adapter.gateway_runner._schedule_flood_redelivery.assert_not_called()
    assert dl.pending_retries() == []
    assert dl.sweep_failed_for_runtime('discord') == []
    monkeypatch.setattr(dl, '_owner_alive', lambda *args: False)
    assert dl.sweep_recoverable() == []


@pytest.mark.asyncio
async def test_uncertain_streamed_final_keeps_source_anchor_on_recovery(monkeypatch):
    from gateway.platforms.base import SendResult
    adapter = _Adapter(); event = _event(); event.source.platform = Platform.DISCORD
    adapter._extract_response_content = AsyncMock(return_value=SimpleNamespace(text_content='uncertain answer'))
    await adapter.ledger_attempted_final(event, 'review', 'uncertain answer',
                                         error='Timed out: the streamed send may not have landed')
    oid = dl.compute_obligation_id('review', event.message_id, 'uncertain answer')
    meta = dl.get_delivery_metadata(oid)
    assert meta['delivery_id'] == oid and meta['notify'] is True
    assert meta['source_message_id'] == str(event.message_id)
    monkeypatch.setattr(dl, '_owner_alive', lambda *args: False)
    rows = dl.sweep_recoverable()
    assert len(rows) == 1
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id='recovered'))
    assert await Runner(adapter)._redeliver_claimed_obligations(rows) == 1
    kwargs = adapter.send.await_args.kwargs
    assert kwargs['reply_to'] == str(event.message_id)
    assert kwargs['metadata']['source_message_id'] == str(event.message_id)
    assert kwargs['metadata']['delivery_id'] == oid


@pytest.mark.asyncio
async def test_uncertain_discord_replay_completes_source(adapter, monkeypatch):  # noqa: F811
    a, channel = adapter
    monkeypatch.setattr(a, '_missed_message_backfill_enabled', lambda: True)
    event = _event(); event.source.platform = Platform.DISCORD
    event.source.chat_id = '555'; event.message_id = '999'
    content = '[[discord-recipients:["Jeff"]]]\nThe report is ready for Jeff.'
    a._extract_response_content = AsyncMock(return_value=SimpleNamespace(text_content=content))
    await a.ledger_attempted_final(event, 'review', content,
                                   error='Timed out: the streamed send may not have landed')
    monkeypatch.setattr(dl, '_owner_alive', lambda *args: False)
    assert await Runner(a)._redeliver_claimed_obligations(dl.sweep_recoverable()) == 1
    assert channel.send.await_count == 1
    assert a._discord_message_is_persistently_complete('999')


@pytest.fixture
def kanban(tmp_path, monkeypatch):
    from gateway import session_context as sc
    from hermes_cli import kanban_db as kb
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    for name in ('HERMES_KANBAN_TASK', 'HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('HERMES_SESSION_USER_ID', 'ambient-owner')
    sc.reset_session_vars()
    kb.init_db()
    yield sc


def _stamp(title):
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc
    from tools.kanban_request_origin import stamp_task_origin, task_origin
    with kbc.connect_closing() as conn:
        task = kb.create_task(conn, title=title)
        stamp_task_origin(conn, task)
        return task_origin(conn, task)


def test_origin_single_sender_stamps_actor(kanban):
    sc = kanban
    tokens = sc.set_session_vars(platform='discord', user_id='sender-A', user_name='A', session_key='k',
                                 session_id='sid', message_id='m1', turn_event=SimpleNamespace(user_id='sender-A'))
    try:
        assert _stamp('single')['user_id'] == 'sender-A'
    finally:
        sc.clear_session_vars(tokens)


def test_origin_joined_turn_fails_closed(kanban):
    sc = kanban
    event = SimpleNamespace(user_id='sender-A')
    tokens = sc.set_session_vars(platform='discord', user_id='sender-A', user_name='A', session_key='k',
                                 session_id='sid', message_id='m1', turn_event=event)
    try:
        event.user_id = ''  # another sender's input joined the running turn
        assert sc.get_bound_session_env('HERMES_SESSION_ACTOR_ID') == ''
        assert _stamp('joined') is None  # never attributed to the routing user
    finally:
        sc.clear_session_vars(tokens)


def test_origin_actor_differs_from_route(kanban):
    sc = kanban
    tokens = sc.set_session_vars(platform='discord', user_id='route-U', user_name='U', session_key='k',
                                 session_id='sid', message_id='m1', actor_id='actor-B', actor_name='B',
                                 turn_event=SimpleNamespace(user_id='actor-B'))
    try:
        assert _stamp('differing')['user_id'] == 'actor-B'
    finally:
        sc.clear_session_vars(tokens)


def test_origin_unbound_never_reads_environ(kanban):
    assert kanban.get_bound_session_env('HERMES_SESSION_USER_ID', 'unset') == 'unset'
    assert _stamp('unbound') is None  # ambient HERMES_SESSION_USER_ID is never an origin
