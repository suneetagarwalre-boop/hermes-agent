"""Real notification producer -> strict adapter boundary, no Discord network."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord  # Load the installed SDK before legacy fake-adapter fixtures.
import pytest
from gateway.config import PlatformConfig
from gateway.kanban_watchers_notifier import _KanbanNotification
from plugins.platforms.discord.adapter import DiscordAdapter, _apply_yaml_config
from plugins.platforms.discord.recipient_contract import RecipientContractError

JEFF = '1555599898265522327'

@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path/'home'))
    roster = tmp_path/'roster.json'
    roster.write_text(json.dumps({'members':[{'id':JEFF, 'aliases':['Jeff']}]}))
    config = PlatformConfig(enabled=True, token='test', extra={
        'action_mentions':True, 'recipient_contract':True, 'mention_roster_path':str(roster)})
    adapter = DiscordAdapter(config)
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=123)))
    adapter._client = SimpleNamespace(get_channel=lambda _:channel, fetch_channel=AsyncMock())
    monkeypatch.setattr(adapter, '_is_forum_parent', lambda _:False)
    return adapter, channel

@pytest.mark.asyncio
@pytest.mark.parametrize('body', ['Jeff, upload the file.', 'Jeff needs to approve the draft.', 'The report is ready for Jeff.'])
async def test_persisted_background_subscription_actual_producer(adapter, monkeypatch, body):
    a, channel = adapter
    n = _KanbanNotification.__new__(_KanbanNotification)
    # Disk roundtrip of the real subscription routing fields, not summary NLP.
    n.sub = json.loads(json.dumps({'chat_id':'555','thread_id':'777','user_id':JEFF}))
    n.adapter = a
    n.platform_str = 'discord'
    n.task_id = 'test-result'
    n.board_slug = 'test-board'
    n.runner = SimpleNamespace(_kanban_confirm_delivery=lambda *args:None)
    monkeypatch.setattr('gateway.kanban_watchers_notifier._to_thread_process_service', AsyncMock())
    monkeypatch.setattr(n, '_upload_artifacts', AsyncMock(), raising=False)
    # _send_event includes artifact handling after the text confirmation.
    ev = SimpleNamespace(id=1,kind='completed',payload={})
    await n._send_event(ev, body+'\n'+'details '*700)
    sends = channel.send.await_args_list
    assert len(sends)>1
    assert sum(c.kwargs['content'].count(f'<@{JEFF}>') for c in sends)==1
    assert all('discord-recipients' not in c.kwargs['content'] for c in sends)
    assert all(c.kwargs['allowed_mentions'].to_dict().get('users') == [int(JEFF)] for c in sends)
    assert body in sends[0].kwargs['content']

@pytest.mark.asyncio
async def test_invalid_final_has_one_visible_notice_not_action_or_noisy_retries(adapter):
    a, channel = adapter
    for _ in range(2):
        result = await a.send('555','Jeff needs to approve the draft.',reply_to='999',metadata={'notify':True})
        assert not result.success
        assert result.raw_response['recipient_failure_notice_id']=='123'
    assert channel.send.await_count==1
    call = channel.send.await_args.kwargs
    assert 'delivery blocked' in call['content']
    assert 'approve the draft' not in call['content']
    assert call['allowed_mentions'].to_dict()['parse']==[]

@pytest.mark.parametrize('recipient', [None, 'unknown', '1555599898265522328'])
def test_background_missing_unbound_recipient_is_explicit(adapter, recipient):
    a, channel = adapter
    with pytest.raises(RecipientContractError):
        a.result_notification_text('The report is ready for Jeff.',recipient)
    channel.send.assert_not_called()


def test_strict_switch_reaches_adapter_extra():
    seeded = _apply_yaml_config({}, {'recipient_contract':True})
    assert seeded['recipient_contract'] is True


def test_other_profile_background_is_unchanged(adapter):
    a, _ = adapter
    a.config.extra.pop('recipient_contract')
    assert a.result_notification_text('Jeff said hello.',None)=='Jeff said hello.'


@pytest.mark.asyncio
async def test_real_final_extraction_and_ledger_boundary_preserve_contract(adapter, monkeypatch):
    from gateway.config import Platform
    from gateway.session import SessionSource
    from gateway.platforms.event import MessageEvent, MessageType
    a, channel = adapter
    source = SessionSource(platform=Platform.DISCORD,chat_id='555',chat_type='group',user_id=JEFF)
    event = MessageEvent(text='test',message_type=MessageType.TEXT,source=source,message_id='999')
    text = '[[discord-recipients:["Jeff"]]]\nThe report is ready for Jeff.'
    extracted = await a._extract_response_content(text,event,'test-session',is_ephemeral_response=False)
    assert extracted.text_content.startswith('[[discord-recipients:')
    monkeypatch.setattr(a,'_record_delivery_obligation',AsyncMock(return_value='obligation'))
    finalize = AsyncMock()
    monkeypatch.setattr(a,'_finalize_delivery_obligation',finalize)
    result, _ = await a.send_final_ledgered(event,'test-session',extracted.text_content,{'notify':True},reply_to='999')
    assert result.success
    assert channel.send.await_count==1
    assert channel.send.await_args.kwargs['content'].startswith(f'<@{JEFF}>\n')
    finalize.assert_awaited_once()
