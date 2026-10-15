"""Production wrapper, restored-session producer, and actual recovery boundaries."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from tests.gateway.test_discord_recipient_producers import adapter, JEFF
from gateway import delivery_ledger as dl
from gateway.run_startup import GatewayStartupMixin

@pytest.mark.asyncio
async def test_no_message_id_refusal_terminal_and_deduplicated(adapter):
    a, channel = adapter
    for _ in range(2):
        result = await a._send_with_retry('555', 'The report is ready for Jeff.', metadata={'notify':True})
        assert not result.success
        assert a._send_retry_is_final(result)
    assert channel.send.await_count == 1

@pytest.mark.asyncio
@pytest.mark.parametrize('thread', [None, '777'])
@pytest.mark.parametrize('declaration,valid', [
    ('[[discord-recipients:["Jeff"]]]\n',True),
    ('[[discord-recipients:["unknown"]]]\n',False),
    ('',False), ('[[discord-recipients:broken]]\n',False)])
async def test_actual_recovery_preserves_contract_marker_and_identity(adapter, monkeypatch, tmp_path, thread, declaration, valid):
    a, channel = adapter
    monkeypatch.setattr(dl, '_db_path', lambda: tmp_path/'ledger.db')
    content = declaration + 'The report is ready for Jeff.'
    dl.record_obligation(obligation_id='logical-event',session_key='test',platform='discord',
        chat_id='555',thread_id=thread,content=content,
        delivery_metadata={'notify':True,'source_message_id':'999','delivery_id':'logical-event'})
    class Runner(GatewayStartupMixin):
        _obligation_adapter = AsyncMock(return_value=a)
        _arm_flood_timers_for_waiting_rows = AsyncMock()
    row={'obligation_id':'logical-event','content':content,'platform':'discord','chat_id':'555',
         'thread_id':thread,'needs_marker':True,'marker':'Recovered: ','attempts':1}
    count = await Runner()._redeliver_claimed_obligations([row])
    assert count == int(valid)
    assert channel.send.await_count == 1
    sent = channel.send.await_args.kwargs
    if valid:
        assert sent['content'] == 'Recovered: <@'+JEFF+'>\nThe report is ready for Jeff.'
        assert 'users' in sent['allowed_mentions'].to_dict()['parse']
    else:
        assert 'delivery blocked' in sent['content']
        assert sent['allowed_mentions'].to_dict()['parse']==[]
    with dl._connect() as conn:
        state = conn.execute('SELECT state FROM delivery_obligations').fetchone()[0]
    assert state == ('delivered' if valid else 'refused')
    if not valid:
        assert dl.pending_retries()==[]
        assert dl.sweep_failed_for_runtime('discord')==[]
        # Even a boot with a dead owner cannot bypass a logical refusal.
        monkeypatch.setattr(dl,'_owner_alive',lambda *args:False)
        assert dl.sweep_recoverable()==[]

@pytest.mark.asyncio
async def test_replay_does_not_duplicate_original_refusal_notice(adapter,monkeypatch,tmp_path):
    a, channel = adapter
    monkeypatch.setattr(dl,'_db_path',lambda:tmp_path/'ledger.db')
    content='The report is ready for Jeff.'
    meta={'notify':True,'delivery_id':'logical-event'}
    result=await a._send_with_retry('555',content,metadata=meta)
    assert not result.success
    dl.record_obligation(obligation_id='logical-event',session_key='test',platform='discord',
        chat_id='555',thread_id=None,content=content,delivery_metadata=meta)
    class Runner(GatewayStartupMixin):
        _obligation_adapter=AsyncMock(return_value=a)
        _arm_flood_timers_for_waiting_rows=AsyncMock()
    await Runner()._redeliver_claimed_obligations([dict(obligation_id='logical-event',content=content,
        platform='discord',chat_id='555',needs_marker=True,attempts=1)])
    assert channel.send.await_count==1

def test_terminal_refusal_is_not_automatically_backfilled_or_marked_delivered(adapter,monkeypatch):
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import ProcessingOutcome
    a, _=adapter
    monkeypatch.setattr(a,'_missed_message_backfill_enabled',lambda:True)
    a._record_discord_response(reply_to='999',result=SendResult(success=False,error='discord_recipient_contract_refused:missing'),content='withheld',final=True)
    a._record_discord_processing_complete(SimpleNamespace(message_id='999',raw_message=None),ProcessingOutcome.FAILURE)
    assert a._discord_message_is_persistently_complete('999')
    row=a._with_discord_recovery_db(lambda conn:conn.execute("SELECT status,replied FROM discord_messages WHERE message_id='999'").fetchone())
    assert tuple(row)==('refused',0)

@pytest.mark.parametrize('strict',[True,False])
def test_real_gateway_turn_refreshes_cached_producer_policy(adapter,monkeypatch,strict):
    from gateway.run_turn_runner import TurnRunner
    a, _=adapter
    a.config.extra['recipient_contract']=strict
    ctx=SimpleNamespace(session_key='test',session_id='restored',source=SimpleNamespace(user_id='owner',user_name='Owner'),
        persist_user_display_kind=None,persist_user_display_metadata=None,moa_config=None,
        inbound_message_id=None,mute_notification_reply=False)
    tr=TurnRunner(SimpleNamespace(_delivery_adapter_for=lambda source:a),ctx)
    monkeypatch.setattr(tr,'_native_image_run_message',lambda:'Internal Kanban result handoff for Jeff.')
    seen=[]
    class Agent:
        ephemeral_system_prompt='Original ephemeral policy'
        _cached_system_prompt='OLD cached prompt WITHOUT transport metadata'
        def run_conversation(self,message,**kwargs):
            seen.append(self.ephemeral_system_prompt)
            return {'final_response':'test'}
    agent=Agent()
    tr._run_conversation_with_approval(agent,[],None,None,None)
    assert ('discord-recipients' in seen[0]) is strict
    assert agent.ephemeral_system_prompt=='Original ephemeral policy'
    assert agent._cached_system_prompt=='OLD cached prompt WITHOUT transport metadata'
