"""Room-scoped triggers must not discard passive bot history or widen access."""
from types import SimpleNamespace

import pytest
from tests.gateway.test_discord_free_response import (
    adapter, FakeDMChannel, FakeTextChannel, FakeThread, FakeHistoryChannel,
    make_message, make_history_message,
)
from plugins.platforms.discord.adapter import _apply_yaml_config

ROOM = '1506747928541397032'
OWNER = '1055171971282899095'
PEER = '1511441029314908171'
POLICY = {ROOM: {'bot_ids': [PEER], 'direct_user_id': OWNER, 'direct_names': ['Pryor']}}

@pytest.fixture
def scoped(adapter, monkeypatch):
    adapter.config.extra['channel_response_policy'] = POLICY
    adapter.config.extra['no_thread_channels'] = [ROOM]
    adapter.config.extra['history_backfill'] = True
    adapter.config.extra['allow_from'] = '*'
    adapter._allowed_user_ids = {'*'}
    adapter._client.user.bot = True
    monkeypatch.setenv('DISCORD_ALLOW_BOTS', 'mentions')
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '*')
    monkeypatch.setenv('DISCORD_REQUIRE_MENTION', 'true')
    monkeypatch.setenv('DISCORD_AUTO_THREAD', 'false')
    adapter._ready_event.set()
    adapter._bot_loop_guard_ok = lambda message: True
    return adapter

def msg(scoped, text, *, author=OWNER, bot=False, ping=False, channel=None):
    m = make_message(channel=channel or FakeTextChannel(channel_id=int(ROOM)),
                     content=text.replace('@SELF', f'<@{scoped._client.user.id}>'),
                     mentions=[scoped._client.user] if ping else [])
    m.author = SimpleNamespace(id=int(author), bot=bot, display_name='test', name='test')
    m.guild = getattr(m.channel, 'guild', None)
    if m.guild is not None:
        m.guild.id = 10
    return m

@pytest.mark.parametrize('text,author,bot,ping,want', [
    ('plain update', PEER, True, False, False),
    ('reply chip only', PEER, True, True, False),
    ('@SELF probe', PEER, True, False, True),
    ('@SELF probe', '1511511765203026110', True, False, False),
    ('plain human conversation', OWNER, False, False, False),
    ('Pryor, help', OWNER, False, False, True),
    ('pryor help', OWNER, False, False, True),
    ('@Pryor: help', OWNER, False, False, True),
    ('Pryorian event', OWNER, False, False, False),
    ('Letterman told Pryor something', OWNER, False, False, False),
    ('Pryor, help', '42', False, False, False),
    ('@SELF help', OWNER, False, False, True),
    ('@SELF help', '42', False, False, True),
    ('direct reply', OWNER, False, True, True),
    ('direct reply', '42', False, True, False),
])
@pytest.mark.asyncio
async def test_live_ingress_trigger_matrix(scoped, text, author, bot, ping, want):
    m = msg(scoped, text, author=author, bot=bot, ping=ping)
    assert await scoped._dispatch_discord_message(m) is want
    assert scoped.handle_message.await_count == int(want)

@pytest.mark.asyncio
async def test_participated_thread_not_ambient(scoped):
    thread = FakeThread(channel_id=999, parent=FakeTextChannel(channel_id=int(ROOM)))
    scoped._threads.mark('999')
    assert await scoped._dispatch_discord_message(msg(scoped, 'plain followup', channel=thread)) is False
    scoped.handle_message.assert_not_awaited()

@pytest.mark.asyncio
async def test_recovery_and_direct_handle_cannot_bypass(scoped):
    m = msg(scoped, 'reply chip', author=PEER, bot=True, ping=True)
    assert await scoped._handle_message(m, recovered=True) is False
    scoped.handle_message.assert_not_awaited()

@pytest.mark.asyncio
async def test_silent_peer_is_available_on_next_mention(scoped):
    author = SimpleNamespace(id=int(PEER), bot=True, display_name='Letterman', name='Letterman')
    prior = make_history_message(author=author, content='passive-cobalt-731', msg_id=121)
    human = make_history_message(author=SimpleNamespace(id=int(OWNER), bot=False, name='Suneet'),
                                 content='relevant-human-reply', msg_id=122)
    channel = FakeHistoryChannel([prior, human], channel_id=int(ROOM))
    plain = msg(scoped, prior.content, author=PEER, bot=True, channel=channel)
    plain.id = 121
    assert await scoped._dispatch_discord_message(plain) is False
    m = msg(scoped, '@SELF report context', author=PEER, bot=True, channel=channel)
    assert await scoped._dispatch_discord_message(m) is True
    event = scoped.handle_message.await_args.args[0]
    assert 'passive-cobalt-731' in event.channel_context
    assert 'relevant-human-reply' in event.channel_context

@pytest.mark.parametrize('channel', [FakeDMChannel(), FakeTextChannel(channel_id=444)])
def test_other_channels_and_dms_policy_unchanged(scoped, channel):
    assert scoped._channel_response_addressed(msg(scoped, 'plain', channel=channel)) is None

@pytest.mark.asyncio
async def test_channel_allowlist_still_wins(scoped):
    scoped.config.extra['allowed_channels'] = ['444']
    assert await scoped._dispatch_discord_message(msg(scoped, '@SELF test')) is False
    scoped.handle_message.assert_not_awaited()

@pytest.mark.parametrize('value', [None, '', [], {'bot_ids': '*'}])
def test_malformed_policy_does_not_admit_bot(scoped, value):
    scoped.config.extra['channel_response_policy'] = {ROOM: value}
    assert scoped._channel_response_addressed(msg(scoped, '@SELF test', author=PEER, bot=True)) is False

def test_yaml_bridge_profile_local():
    assert _apply_yaml_config({}, {'channel_response_policy': POLICY})['channel_response_policy'] == POLICY


def test_numeric_yaml_ids_match_the_same_room(scoped):
    scoped.config.extra['channel_response_policy'] = {int(ROOM): {
        'bot_ids': [int(PEER)], 'direct_user_id': int(OWNER), 'direct_names': ['Pryor']}}
    assert scoped._channel_response_addressed(msg(scoped, 'plain update', author=PEER, bot=True)) is False
    assert scoped._channel_response_addressed(msg(scoped, '@SELF probe', author=PEER, bot=True)) is True
    assert scoped._channel_response_addressed(msg(scoped, 'Pryor, help')) is True


@pytest.mark.asyncio
async def test_direct_name_does_not_bypass_human_authorization(scoped):
    scoped._allowed_user_ids = {'42'}
    assert await scoped._dispatch_discord_message(msg(scoped, 'Pryor, help')) is False
    scoped.handle_message.assert_not_awaited()
