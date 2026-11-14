"""Long final delivery validates routing before choosing files, without losing source state."""
from types import SimpleNamespace

import discord  # Load the real SDK before legacy fake-adapter fixtures.
import pytest

from tests.gateway.test_discord_recipient_producers import adapter, JEFF


def _source_row(adapter):
    return adapter._with_discord_recovery_db(lambda conn: conn.execute(
        "SELECT status, replied, response_message_id FROM discord_messages WHERE message_id='999'"
    ).fetchone())


@pytest.mark.asyncio
@pytest.mark.parametrize('declaration', ['', '[[discord-recipients:["Unknown"]]]\n'])
async def test_long_invalid_final_is_refused_before_any_upload(adapter, monkeypatch, declaration):
    a, channel = adapter
    monkeypatch.setattr(a, '_missed_message_backfill_enabled', lambda: True)
    text = declaration + 'The private report is ready for Jeff.\n' * 2000
    for _ in range(2):
        result = await a._send_with_retry('555', text, metadata={'notify': True, 'source_message_id': '999'})
        assert not result.success and a._send_retry_is_final(result)
    channel.send.assert_awaited_once()
    call = channel.send.await_args.kwargs
    assert 'delivery blocked' in call['content']
    assert 'private report' not in call['content']
    assert 'files' not in call
    assert call['allowed_mentions'].to_dict()['parse'] == []
    assert tuple(_source_row(a))[:2] == ('refused', 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('recipients', [[], ['Jeff']])
@pytest.mark.parametrize('forum', [False, True])
async def test_long_declared_final_upload_has_scoped_caption_and_clean_file(
        adapter, monkeypatch, recipients, forum):
    import json
    a, channel = adapter
    monkeypatch.setattr(a, '_missed_message_backfill_enabled', lambda: True)
    monkeypatch.setattr(a, '_is_forum_parent', lambda _: forum)
    uploads = []

    async def upload(**kwargs):
        attachment = kwargs['files'][0]
        uploads.append((kwargs, attachment.fp.read()))
        attachment.close()
        message = SimpleNamespace(id=123, attachments=[SimpleNamespace(id=456)])
        if forum:
            return SimpleNamespace(thread=SimpleNamespace(id=777), message=message)
        return message

    from unittest.mock import AsyncMock
    channel.send.side_effect = upload
    channel.create_thread = AsyncMock(side_effect=upload)
    body = ('The report is ready for Jeff. @everyone <@1555599898265522328>\n' * 2000).rstrip()
    text = '[[discord-recipients:' + json.dumps(recipients) + ']]\n' + body
    metadata = {'notify': True, 'source_message_id': '999', 'thread_id': '777'}
    result = await a._send_with_retry('555', text, metadata=metadata)
    assert result.success and result.raw_response['sent_as_file'] is True
    assert len(uploads) == 1
    call, data = uploads[0]
    assert data.decode() == a.format_message(text)
    assert 'discord-recipients' not in data.decode()
    assert body.splitlines()[-1] in data.decode()
    policy = call['allowed_mentions'].to_dict()
    assert policy['parse'] == [] and call['allowed_mentions'].replied_user is False
    assert policy.get('users', []) == ([int(JEFF)] if recipients else [])
    assert call['content'].count(f'<@{JEFF}>') == int(bool(recipients))
    assert 'discord-recipients' not in call['content']
    assert '_prepared_recipient_policy' not in a.config.extra
    assert metadata == {'notify': True, 'source_message_id': '999', 'thread_id': '777'}
    assert tuple(_source_row(a)) == ('responded', 1, '123')


@pytest.mark.asyncio
async def test_suppressed_long_recipient_cannot_upload(adapter):
    a, channel = adapter
    a.config.extra['allow_mentions'] = {'users': False}
    text = '[[discord-recipients:["Jeff"]]]\n' + 'Private result for Jeff.\n' * 3000
    result = await a._send_with_retry('555', text, metadata={'notify': True})
    assert not result.success and a._send_retry_is_final(result)
    channel.send.assert_awaited_once()
    call = channel.send.await_args.kwargs
    assert 'notification suppressed by effective allowed_mentions' in call['content']
    assert 'files' not in call


@pytest.mark.asyncio
async def test_failed_upload_falls_back_to_every_chunk_with_original_recipient_intent(adapter, monkeypatch):
    a, channel = adapter
    monkeypatch.setattr(a, '_missed_message_backfill_enabled', lambda: True)
    uploads = []
    sends = []

    async def send(**kwargs):
        if 'files' in kwargs:
            attachment = kwargs['files'][0]
            uploads.append(attachment.fp.read())
            attachment.close()
            raise RuntimeError('upload refused')
        sends.append(kwargs)
        return SimpleNamespace(id=1000 + len(sends))

    channel.send.side_effect = send
    text = '[[discord-recipients:["Jeff"]]]\n' + 'Jeff report details.\n' * 4000 + 'unique-tail-marker'
    result = await a._send_with_retry('555', text, metadata={'notify': True, 'source_message_id': '999'})
    assert result.success
    assert len(uploads) == 1
    assert len(sends) > a.MAX_SPLIT_MESSAGES
    assert [call['content'] for call in sends] == a.truncate_message(a.format_message(text), a.MAX_MESSAGE_LENGTH)
    assert 'unique-tail-marker' in sends[-1]['content']
    assert sum(call['content'].count(f'<@{JEFF}>') for call in sends) == 1
    assert all(call['allowed_mentions'].to_dict().get('users') == [int(JEFF)] for call in sends)
    assert tuple(_source_row(a))[:2] == ('responded', 1)
