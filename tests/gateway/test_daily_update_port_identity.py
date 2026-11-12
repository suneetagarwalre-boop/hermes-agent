"""Local identity labels survive upstream shared-sender helper extraction."""
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.run_inbound import GatewayInboundMixin
from gateway.session import SessionSource, label_shared_sender


@pytest.mark.parametrize('platform,label', [(Platform.DISCORD, 'Discord'), (Platform.SLACK, 'Slack')])
@pytest.mark.parametrize('backfill', [None, 'room history'])
def test_shared_sender_keeps_envelope_id(platform, label, backfill):
    source = SessionSource(platform=platform, chat_id='room', chat_type='group',
                           user_id='12345', user_name='Name\nInjected')
    expected = f'[Name Injected | {label} user <@12345>] hello'
    assert label_shared_sender(source, 'hello', group_sessions_per_user=False) == expected
    runner = SimpleNamespace(config=SimpleNamespace(group_sessions_per_user=False))
    event = SimpleNamespace(channel_context=backfill)
    result = GatewayInboundMixin._prefix_inbound_sender_context(runner, event, source, 'hello')
    assert result == (f'{backfill}\n\n[New message]\n{expected}' if backfill else expected)


def test_unshared_sender_is_not_relabeled():
    source = SessionSource(platform=Platform.DISCORD, chat_id='room', chat_type='group',
                           user_id='12345', user_name='Name')
    assert label_shared_sender(source, 'hello', group_sessions_per_user=True) == 'hello'
