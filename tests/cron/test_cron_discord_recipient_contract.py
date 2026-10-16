"""Cron -> Discord recipient contract (job 821bb9fb5e62, notice 1560474121421459540).

A no-agent cron's job-level ``discord_recipients`` is its trusted persisted recipient context and
is declared before either lane; a logical contract refusal is terminal and is not retried through
the standalone sender as if it were a transport failure.
"""
import asyncio
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

from cron.scheduler import _deliver_result
from gateway.config import Platform, PlatformConfig
from tests.cron.test_cron_live_delivery_confirmation import _SendResult

CHAT = "1506747928541397032"
EXTRA = {"action_mentions": True, "recipient_contract": True}
REFUSED = "discord_recipient_contract_refused:Missing Discord recipient contract"


def _job(**extra):
    return {"id": "821bb9fb5e62", "name": "BSHG Indeed read-only reply alerts",
            "deliver": f"discord:{CHAT}", "origin": None, **extra}


def _run(job, content, send_result, extra=EXTRA):
    loop = MagicMock()
    loop.is_running.return_value = True

    def fake_run_coro(coro, _loop):
        future = Future()
        try:
            future.set_result(asyncio.run(coro))
        except BaseException as e:  # noqa: BLE001
            future.set_exception(e)
        return future

    router_calls, standalone_calls = [], []
    router = MagicMock()

    async def _deliver_to_platform(target, text, metadata, transport=None):
        router_calls.append(text)
        return send_result

    router._deliver_to_platform = _deliver_to_platform

    async def _fake_send_to_platform(platform, pconfig, chat_id, text, **kwargs):
        standalone_calls.append(text)
        return {"error": REFUSED}

    config = MagicMock()
    config.platforms = {Platform.DISCORD: PlatformConfig(enabled=True, extra=dict(extra))}
    config.get_home_channel = lambda p: None
    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": False}}), \
         patch("cron.scheduler_delivery._record_delivery_verification"), \
         patch("gateway.delivery.DeliveryRouter", return_value=router), \
         patch("tools.send_message_tool._send_to_platform", _fake_send_to_platform), \
         patch("asyncio.run_coroutine_threadsafe", side_effect=fake_run_coro):
        error = _deliver_result(job, content, adapters={Platform.DISCORD: MagicMock()}, loop=loop)
    return error, router_calls, standalone_calls


def test_job_declared_recipients_are_declared_once_before_the_live_lane():
    error, router_calls, standalone_calls = _run(
        _job(discord_recipients=[]), "BSHG Indeed reply — interested", _SendResult(message_id=1))
    assert error is None
    assert router_calls == ['[[discord-recipients:[]]]\nBSHG Indeed reply — interested']
    assert standalone_calls == []


def test_producer_declaration_wins_over_job_declaration():
    text = '[[discord-recipients:["Jeff"]]]\nJeff, review this.'
    _, router_calls, _ = _run(_job(discord_recipients=[]), text, _SendResult(message_id=1))
    assert router_calls == [text]


def test_no_job_key_or_contract_off_leaves_text_unchanged():
    _, calls, _ = _run(_job(), "plain", _SendResult(message_id=1))
    assert calls == ["plain"]
    _, calls, _ = _run(_job(discord_recipients=[]), "plain", _SendResult(message_id=1), extra={})
    assert calls == ["plain"]


def test_malformed_job_declaration_is_not_wrapped():
    _, calls, _ = _run(_job(discord_recipients="Jeff"), "plain", _SendResult(message_id=1))
    assert calls == ["plain"]


def test_logical_refusal_is_terminal_not_retried_standalone():
    error, router_calls, standalone_calls = _run(
        _job(), "Jeff needs to approve.", _SendResult(success=False, error=REFUSED))
    assert len(router_calls) == 1
    assert standalone_calls == []
    assert error and "discord_recipient_contract_refused" in error


def test_transport_failure_still_falls_back_to_standalone():
    _, router_calls, standalone_calls = _run(
        _job(discord_recipients=[]), "alert", _SendResult(success=False, error="send_path_degraded"))
    assert len(router_calls) == 1
    assert standalone_calls == ['[[discord-recipients:[]]]\nalert']
