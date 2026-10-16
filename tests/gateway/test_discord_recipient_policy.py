"""Effective wire policy regressions: declarations must not allow extra pings."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord  # Load the installed SDK before legacy fake-adapter fixtures.
import pytest
from gateway.config import Platform, PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter, _build_allowed_mentions, _standalone_send
from plugins.platforms.discord.recipient_contract import prepare_discord_message
from tools import send_message_tool

JEFF = '1555599898265522327'
OTHER = '1055171971282899095'
DECL = '[[discord-recipients:["Jeff"]]]\n'

@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path/'home'))
    roster = tmp_path/'roster.json'
    roster.write_text(json.dumps({'members':[{'id':JEFF,'aliases':['Jeff']}]}))
    return PlatformConfig(enabled=True, token='test', extra={'action_mentions':True,
        'recipient_contract':True,'mention_roster_path':str(roster)})

def assert_policy(policy, ids=(JEFF,)):
    assert policy['parse'] == []
    assert [str(x) for x in policy.get('users', [])] == list(ids)
    assert policy.get('replied_user', False) is False

@pytest.mark.parametrize('decl,ids', [(DECL,(JEFF,)),('[[discord-recipients:[]]]\n',())])
def test_exact_policy_and_no_implicit_reply_ping(config, decl, ids):
    policy = _build_allowed_mentions(config.extra, decl+f'Ready. <@{OTHER}> @everyone <@&123>')
    assert_policy(policy.to_dict(), ids)

def test_repeated_recipient_tokens_are_inert_in_body(config):
    text = DECL+f'Ready <@{JEFF}> and <@!{JEFF}>'
    prepared = prepare_discord_message(text, config.extra, _build_allowed_mentions(config.extra))
    assert prepared.count(f'<@{JEFF}>') == 1
    assert f'<@!{JEFF}>' not in prepared

@pytest.mark.parametrize('token', ['<@1555599898265522327>', 'Jeff ', '155559989826552232x', '@Jeff'])
def test_invalid_recipient_tokens_not_repaired(config, token):
    text = '[[discord-recipients:'+json.dumps([token])+']]\nReady.'
    with pytest.raises(ValueError, match='Unresolved'):
        _build_allowed_mentions(config.extra, text)

def test_bad_roster_id_refused(config):
    with open(config.extra['mention_roster_path'], 'w') as f:
        json.dump({'members':[{'id':'123','aliases':['Jeff']}]},f)
    with pytest.raises(ValueError, match='Unresolved'):
        _build_allowed_mentions(config.extra, DECL+'Ready.')

def test_unrelated_profile_policy_unchanged(config):
    config.extra['action_mentions'] = False
    policy = _build_allowed_mentions(config.extra, DECL+'Ready.').to_dict()
    assert 'users' in policy['parse']
    assert policy['replied_user'] is True

@pytest.mark.asyncio
async def test_real_live_chunks_extra_user_no_duplicate_recipient(config, monkeypatch):
    adapter = DiscordAdapter(config)
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=123)))
    adapter._client = SimpleNamespace(get_channel=lambda _:channel,fetch_channel=AsyncMock())
    monkeypatch.setattr(adapter, '_is_forum_parent', lambda _:False)
    text = DECL + f'Ready <@{JEFF}>.\n'+'details '*700+f'<@{OTHER}>'
    result = await adapter.send('555',text,reply_to='777',metadata={'notify':True})
    assert result.success
    calls = channel.send.await_args_list
    assert len(calls)>1
    assert sum(c.kwargs['content'].count(f'<@{JEFF}>') for c in calls)==1
    for c in calls:
        assert_policy(c.kwargs['allowed_mentions'].to_dict())

@pytest.mark.asyncio
async def test_real_forum_followups_explicit_policy(config):
    adapter = DiscordAdapter(config)
    thread = SimpleNamespace(id=123,send=AsyncMock(return_value=SimpleNamespace(id=124)))
    forum = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
    result = await adapter._send_to_forum(forum, DECL+'Details '*1000+f'<@{OTHER}>')
    assert result.success
    assert_policy(forum.create_thread.await_args.kwargs['allowed_mentions'].to_dict())
    assert thread.send.await_count>0
    for call in thread.send.await_args_list:
        assert_policy(call.kwargs['allowed_mentions'].to_dict())

@pytest.mark.asyncio
async def test_background_tool_carries_whitelist_without_mutating_shared_config(config, monkeypatch):
    dispatch=AsyncMock(return_value={'success':True})
    monkeypatch.setattr(send_message_tool,'_send_plugin_standalone',dispatch)
    result=await send_message_tool._send_to_platform(Platform.DISCORD,config,'555',DECL+'x'*5000+f'<@{OTHER}>')
    assert result['success']
    prepared_config=dispatch.await_args.args[1]
    assert_policy(_build_allowed_mentions(prepared_config.extra).to_dict())
    assert '_prepared_recipient_policy' not in config.extra
    assert config.extra['action_mentions'] is True

@pytest.mark.asyncio
async def test_suppression_not_widened_by_prepared_policy(config):
    config.extra.update({'action_mentions':False,'allow_mentions':{'users':False},
        '_prepared_recipient_policy':{'users':[JEFF]}})
    result=await _standalone_send(config,'555',f'<@{JEFF}> Ready.')
    assert 'suppressed' in str(result)

@pytest.mark.asyncio
@pytest.mark.parametrize('forum', [False, True])
async def test_standalone_real_rest_payload_whitelist(config, monkeypatch, forum):
    import aiohttp
    import plugins.platforms.discord.adapter as module
    from unittest.mock import MagicMock
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    response = MagicMock()
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session.post.return_value = response
    monkeypatch.setattr(aiohttp, 'ClientSession', lambda **kw:session)
    monkeypatch.setattr(module, '_standalone_is_forum', AsyncMock(return_value=forum))
    monkeypatch.setattr(module, '_standalone_response_json_or_error',
        AsyncMock(return_value=({'id':'123','message':{'id':'124'}},None)))
    result = await _standalone_send(config,'555',DECL+f'Ready. <@{OTHER}> @everyone')
    assert result['success']
    payload = session.post.call_args.kwargs['json']
    if forum:
        payload = payload['message']
    assert_policy(payload['allowed_mentions'])
    assert payload['content'].startswith(f'<@{JEFF}>')
    session.post.assert_called_once()
