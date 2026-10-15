import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gateway.config import Platform, PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter, _build_allowed_mentions, _standalone_send
from plugins.platforms.discord.recipient_contract import prepare_discord_message
from tools import send_message_tool

JEFF = '1555599898265522327'
DECL = '[[discord-recipients:["Jeff"]]]\n'

@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path/'home'))
    roster = tmp_path/'roster.json'
    roster.write_text(json.dumps({'members':[{'id':JEFF,'aliases':['Jeff']}]}))
    return PlatformConfig(enabled=True, token='test', extra={'action_mentions':True, 'mention_roster_path':str(roster)})

@pytest.mark.parametrize('body', ['Jeff, upload the file.', 'Jeff needs to approve the draft.', 'The report is ready for Jeff.'])
@pytest.mark.asyncio
async def test_producer_to_real_adapter_boundary(config, monkeypatch, body):
    adapter = DiscordAdapter(config)
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=123)))
    adapter._client = SimpleNamespace(get_channel=lambda _:channel,fetch_channel=AsyncMock())
    monkeypatch.setattr(adapter, '_is_forum_parent', lambda _:False)
    result = await adapter.send('555', DECL+body+'\n'+'details '*600, metadata={'notify':True})
    assert result.success
    calls = channel.send.await_args_list
    assert len(calls) >= 2
    assert sum(c.kwargs['content'].count(f'<@{JEFF}>') for c in calls) == 1
    assert body in calls[0].kwargs['content']
    for call in calls:
        policy = call.kwargs['allowed_mentions'].to_dict()
        assert policy.get('users') == [int(JEFF)]
        assert 'users' not in policy['parse']
        assert policy.get('replied_user', False) is False
        assert not {'everyone','roles'}.intersection(policy['parse'])

@pytest.mark.parametrize('declaration', ['[[discord-recipients:["Unknown"]]]\n', '[[discord-recipients:["1555599898265522328"]]]\n', '[[discord-recipients:broken]]\n'])
@pytest.mark.asyncio
async def test_tool_fails_before_any_chunk(config, monkeypatch, declaration):
    send = AsyncMock()
    monkeypatch.setattr(send_message_tool, '_send_plugin_standalone', send)
    result = await send_message_tool._send_to_platform(Platform.DISCORD, config, '555', declaration+'x'*5000)
    assert 'error' in result
    send.assert_not_awaited()

@pytest.mark.asyncio
async def test_background_tool_preserves_contract_before_split(config, monkeypatch):
    send = AsyncMock(return_value={'success':True})
    monkeypatch.setattr(send_message_tool, '_send_plugin_standalone', send)
    monkeypatch.setattr(send_message_tool, '_platform_max_length', lambda _:2000)
    # Same serialized output can be persisted/reloaded by completion producers.
    text = json.loads(json.dumps(DECL+'The report is ready for Jeff.\n'+'x'*5000))
    result = await send_message_tool._send_to_platform(Platform.DISCORD, config, '555', text)
    assert result['success']
    args = send.await_args.args
    chunks = args[4]
    assert len(chunks) >= 3
    assert sum(c.count(f'<@{JEFF}>') for c in chunks) == 1
    assert all('discord-recipients' not in c for c in chunks)

@pytest.mark.asyncio
async def test_suppressed_policy_fails_standalone_without_network(config):
    config.extra['allow_mentions'] = {'users':False}
    result = await _standalone_send(config, '555', DECL+'The report is ready for Jeff.')
    assert 'suppressed' in str(result)


def test_unknown_ambiguous_and_narrative(config):
    policy = _build_allowed_mentions(config.extra)
    assert prepare_discord_message('Jeff said hello.', config.extra, policy) == 'Jeff said hello.'
    assert prepare_discord_message('[[discord-recipients:[]]]\nNo recipient.',config.extra,policy) == 'No recipient.'
    roster = config.extra['mention_roster_path']
    with open(roster, 'w') as f:
        json.dump({'members':[{'id':JEFF,'aliases':['Jeff']},{'id':'1055171971282899095','aliases':['Jeff']}]},f)
    with pytest.raises(ValueError, match='ambiguous'):
        prepare_discord_message(DECL+'Ready.',config.extra,policy)


def test_other_profile_not_opted_in(config):
    config.extra['action_mentions'] = False
    assert prepare_discord_message(DECL+'Ready.',config.extra,{}) == DECL+'Ready.'


@pytest.mark.asyncio
async def test_strict_final_omission_is_explicit_failure_not_unpinged_delivery(config, monkeypatch):
    config.extra['recipient_contract'] = True
    adapter = DiscordAdapter(config)
    channel = SimpleNamespace(send=AsyncMock())
    adapter._client = SimpleNamespace(get_channel=lambda _:channel,fetch_channel=AsyncMock())
    result = await adapter.send('555', 'Jeff needs to approve the draft.', metadata={'notify':True})
    assert not result.success and 'Missing Discord recipient contract' in result.error
    channel.send.assert_awaited_once()
    assert 'Reply delivery blocked' in channel.send.await_args.kwargs['content']
    assert 'approve the draft' not in channel.send.await_args.kwargs['content']
    dispatch = AsyncMock()
    monkeypatch.setattr(send_message_tool, '_send_plugin_standalone', dispatch)
    result = await send_message_tool._send_to_platform(Platform.DISCORD,config,'555','The report is ready for Jeff.')
    assert 'Missing Discord recipient contract' in result['error']
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_strict_final_edit_cannot_claim_notification(config):
    config.extra['recipient_contract'] = True
    adapter = DiscordAdapter(config)
    msg = SimpleNamespace(edit=AsyncMock())
    channel = SimpleNamespace(get_partial_message=lambda _:msg)
    adapter._client = SimpleNamespace(get_channel=lambda _:channel,fetch_channel=AsyncMock())
    result = await adapter.edit_message('555','123',DECL+'Ready.',finalize=True)
    assert not result.success and 'fresh Discord send' in result.error
    msg.edit.assert_not_awaited()

