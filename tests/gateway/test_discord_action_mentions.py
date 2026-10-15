import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from plugins.platforms.discord.mention_rewriter import rewrite_plain_mentions
from plugins.platforms.discord.adapter import DiscordAdapter, _build_allowed_mentions
from gateway.config import PlatformConfig

JEFF = '1555599898265522327'
@pytest.fixture
def roster(tmp_path):
    p=tmp_path/'roster.json'
    p.write_text(json.dumps({'members':[{'id':JEFF,'aliases':['Jeff','Jeffrey Rega']}]}))
    return p

@pytest.mark.parametrize('text', ['Jeff, please check this.', 'Jeff: can you test this?', '- Jeff, review the draft.', 'Jeffrey Rega, your report is ready.'])
def test_action_vocatives(roster,text):
    result=rewrite_plain_mentions(text,roster,actionable=True)
    assert f'<@{JEFF}>' in result
    assert rewrite_plain_mentions(result,roster,actionable=True) == result
    assert rewrite_plain_mentions(text,roster) == text

@pytest.mark.parametrize('text', ['Jeff said yes.', 'Jeff, thanks.', '`Jeff, please check`', '```\nJeff: review this\n```', 'https://example.com/Jeff,please', 'Unknown, please test', 'Jefferson, please test', '<@invalid> please test'])
def test_no_speculative_pings(roster,text):
    assert rewrite_plain_mentions(text,roster,actionable=True)==text

def test_ambiguous_alias_fail_closed(roster):
    roster.write_text(json.dumps({'members':[{'id':JEFF,'aliases':['Jeff']},{'id':'1055171971282899095','aliases':['Jeff']}]}))
    assert rewrite_plain_mentions('Jeff, please test',roster,actionable=True)=='Jeff, please test'

@pytest.mark.asyncio
async def test_real_adapter_split_send_and_allowed_mentions(roster,monkeypatch,tmp_path):
    monkeypatch.setenv('HERMES_HOME',str(tmp_path/'home'))
    adapter=DiscordAdapter(PlatformConfig(enabled=True,token='test',extra={'mention_roster_path':str(roster),'action_mentions':True}))
    channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=123)))
    adapter._client=SimpleNamespace(get_channel=lambda _:channel,fetch_channel=AsyncMock())
    monkeypatch.setattr(adapter,'_is_forum_parent',lambda _:False)
    text='Jeff, please test this.\n' + 'x'*4000
    result=await adapter.send('555',text)
    assert result.success
    assert channel.send.await_count >= 2
    chunks=[c.kwargs['content'] for c in channel.send.await_args_list]
    assert sum(x.count(f'<@{JEFF}>') for x in chunks)==1
    allowed=_build_allowed_mentions(adapter.config.extra).to_dict()
    assert 'users' in allowed['parse']
    assert 'everyone' not in allowed['parse']
    assert 'roles' not in allowed['parse']
