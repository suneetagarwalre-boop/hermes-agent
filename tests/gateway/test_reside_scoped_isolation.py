"""Additional transport isolation / pagination / backend validation tests."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from gateway import reside_scope as scope
from tests.gateway.test_reside_scoped_ops import broker, principal, data, row, request
from tests.gateway.test_discord_inert_human_ack import scoped
from tests.gateway.test_discord_free_response import adapter


@pytest.mark.asyncio
async def test_channel_history_reference_memory_and_attachments_never_loaded(broker,monkeypatch):
    m,dm=request(broker,'reside draft {"objective":"Internal SOP","context":"Provided fact"}')
    m.channel.history=Mock(side_effect=AssertionError('shared history leaked'))
    m.reference=SimpleNamespace(resolved=SimpleNamespace(content='OWNER_PRIVATE_HISTORY_SECRET'))
    broker.build_source=Mock(side_effect=AssertionError('normal session constructed'))
    await broker._dispatch_discord_message(m)
    text=dm.send.await_args.kwargs['content']
    assert 'OWNER_PRIVATE_HISTORY_SECRET' not in text
    assert 'Provided fact' in text
    m.channel.history.assert_not_called()
    broker.build_source.assert_not_called()
    broker.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_scope_payloads_never_mix_with_shared_session(broker):
    m1,dm1=request(broker,'reside draft {"objective":"Alpha","context":"First input"}')
    m2,dm2=request(broker,'reside draft {"objective":"Beta","context":"Second input"}')
    m2.id+=1
    await asyncio.gather(broker._dispatch_discord_message(m1),broker._dispatch_discord_message(m2))
    assert 'Alpha' in dm1.send.await_args.kwargs['content']
    assert 'Beta' not in dm1.send.await_args.kwargs['content']
    assert 'Alpha' not in dm2.send.await_args.kwargs['content']
    assert 'Beta' in dm2.send.await_args.kwargs['content']
    broker.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_error_never_falls_back_to_agent(broker,monkeypatch):
    m,dm=request(broker,'reside report')
    monkeypatch.setattr(scope,'restricted',Mock(side_effect=RuntimeError('broken policy')))
    with pytest.raises(RuntimeError):
        await broker._dispatch_discord_message(m)
    broker.handle_message.assert_not_awaited()
    broker._handle_message.assert_not_awaited()
    dm.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_failure_does_not_repeat_work(broker,monkeypatch):
    report=Mock(return_value='safe counts')
    monkeypatch.setattr(scope,'automation_report',report)
    m,dm=request(broker,'reside report')
    dm.send.side_effect=RuntimeError('network down')
    await broker._dispatch_discord_message(m)
    await broker._dispatch_discord_message(m)
    assert report.call_count==1 and dm.send.await_count==1
    m.channel.send.assert_not_awaited()
    broker.handle_message.assert_not_awaited()


def test_pagination_limit_does_not_claim_total(principal):
    first=data([row()],cursor='1')
    calls=[]
    def post(q,v,t):
        calls.append(v)
        if not v: return first
        return {'next_items_page':{'cursor':str(len(calls)), 'items':[row()]}}
    with pytest.raises(ValueError,match='incomplete'):
        scope.automation_report(principal,'x',post)


@pytest.mark.parametrize('raw',[b'{"errors":[{"message":"SECRET"}]}', b'not json',b'[]',b'x'*2_000_001])
def test_backend_error_or_oversized_response_is_not_a_projection(monkeypatch,raw):
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,size): return raw
    opener=SimpleNamespace(open=Mock(return_value=Response()))
    monkeypatch.setattr(scope.urllib.request,'build_opener',lambda *args:opener)
    with pytest.raises((ValueError,TypeError)):
        scope._post(scope.FIRST,{},'test-token')
    sent=opener.open.call_args.args[0]
    assert sent.full_url=='https://api.monday.com/v2'
    assert json.loads(sent.data)['query']==scope.FIRST


def test_redirect_handler_refuses_destination_changes(monkeypatch):
    def build(handler):
        with pytest.raises(PermissionError):
            handler.redirect_request(None,None,302,'redirect',{},'https://attacker.example')
        raise PermissionError('test end')
    monkeypatch.setattr(scope.urllib.request,'build_opener',build)
    with pytest.raises(PermissionError):
        scope._post(scope.FIRST,{},'test-token')


def test_no_token_no_network(monkeypatch):
    build=Mock(side_effect=AssertionError('network used'))
    monkeypatch.setattr(scope.urllib.request,'build_opener',build)
    with pytest.raises(PermissionError): scope._post(scope.FIRST,{},'')
    build.assert_not_called()


def test_maximum_draft_length_fits_private_response(principal):
    assert len(scope.draft(principal,{'objective':'x'*600,'context':'x'*600}))<1900
