"""Bounded repair of UNDECLARED final messages (notice 1560474121421459540, cron 821bb9fb5e62).

The repair may declare recipients from trusted persisted context, or ``[]`` only when the text
names nobody on the roster. Everything else still fails closed, with a notice that does not ask
the user to rerun.
"""
import pytest

from tests.gateway.test_discord_recipient_producers import adapter, JEFF  # noqa: F401
from plugins.platforms.discord.recipient_contract import repair_missing_declaration

INDEED_ALERT = (
    "BSHG Indeed reply — interested (high)\nPat Rivera | pat@example.com\n"
    "Reply: Yes, I'd like to talk this week.\n"
    "GHL contact: https://app.gohighlevel.com/v2/location/LOC/contacts/detail/abc\n"
    "Read-only monitor: no prospect response sent.")


@pytest.mark.asyncio
async def test_exact_cron_alert_without_declaration_is_delivered_without_pings(adapter):
    a, channel = adapter
    result = await a.send('555', INDEED_ALERT)  # cron live lane: metadata carries job_id/notify
    assert result.success
    sent = channel.send.await_args.kwargs
    assert 'discord-recipients' not in sent['content']
    assert 'Pat Rivera' in sent['content']
    assert sent['allowed_mentions'].to_dict().get('users', []) == []
    assert channel.send.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [
    'Jeff needs to approve the draft.', 'The report is ready for jeff.',
    'ping <@' + JEFF + '> now', 'heads up @everyone', 'id ' + JEFF + ' owes this',
    'Body [[discord-recipients:[]]] nested'])
async def test_text_naming_someone_still_fails_closed_with_honest_notice(adapter, text):
    a, channel = adapter
    result = await a.send('555', text, reply_to='999', metadata={'notify': True})
    assert not result.success
    assert result.error.startswith('discord_recipient_contract_refused:')
    notice = channel.send.await_args.kwargs
    assert 'delivery blocked' in notice['content']
    assert 'ask me to retry' not in notice['content']
    assert notice['allowed_mentions'].to_dict()['parse'] == []
    assert channel.send.await_count == 1  # the notice only; the content was withheld


@pytest.mark.asyncio
async def test_trusted_context_declares_and_revalidates(adapter):
    a, channel = adapter
    result = await a.send('555', 'The report is ready for Jeff.',
                          metadata={'notify': True, 'discord_recipients': ['Jeff']})
    assert result.success
    sent = channel.send.await_args.kwargs
    assert sent['content'].count('<@' + JEFF + '>') == 1
    assert sent['allowed_mentions'].to_dict().get('users') == [int(JEFF)]


@pytest.mark.asyncio
@pytest.mark.parametrize('trusted', ['Jeff', ['unknown'], [''], [5]])
async def test_untrusted_or_unknown_context_is_not_repaired(adapter, trusted):
    a, channel = adapter
    result = await a.send('555', 'The report is ready.',
                          metadata={'notify': True, 'discord_recipients': trusted})
    assert not result.success
    assert 'delivery blocked' in channel.send.await_args.kwargs['content']


def test_repair_fails_closed_without_a_readable_roster(tmp_path):
    extra = {'action_mentions': True, 'recipient_contract': True,
             'mention_roster_path': str(tmp_path / 'missing.json')}
    assert repair_missing_declaration(INDEED_ALERT, extra) == (INDEED_ALERT, None)


def test_repair_is_inert_when_contract_disabled_or_declared(tmp_path):
    assert repair_missing_declaration('hi', {'action_mentions': True}) == ('hi', None)
    declared = '[[discord-recipients:[]]]\nhi'
    extra = {'action_mentions': True, 'recipient_contract': True}
    assert repair_missing_declaration(declared, extra, ['Jeff']) == (declared, None)


@pytest.mark.asyncio
async def test_standalone_sender_still_refuses_named_undeclared_text(adapter):
    a, _ = adapter
    from gateway.config import Platform
    from tools.send_message_tool import _send_to_platform
    result = await _send_to_platform(Platform.DISCORD, a.config, '555', 'Jeff, approve the draft.')
    assert 'recipient contract' in result['error'].lower()
