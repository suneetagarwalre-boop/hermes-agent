"""Regression tests for profile-scoped Discord mention aliases.

These tests exercise ``DiscordAdapter._handle_message`` — the same inbound
preprocessing path used by a live gateway — rather than calling the alias
normalizer directly.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import asyncio
import sys

import pytest

from gateway.config import PlatformConfig


def _ensure_discord_mock():
    if "discord" in sys.modules and hasattr(sys.modules["discord"], "__file__"):
        return

    discord_mod = MagicMock()
    discord_mod.Intents.default.return_value = MagicMock()
    discord_mod.DMChannel = type("DMChannel", (), {})
    discord_mod.Thread = type("Thread", (), {})
    discord_mod.ForumChannel = type("ForumChannel", (), {})
    discord_mod.MessageType = SimpleNamespace(default=0, reply=19)
    discord_mod.ui = SimpleNamespace(
        View=object,
        button=lambda *a, **k: (lambda fn: fn),
        Button=object,
    )
    discord_mod.ButtonStyle = SimpleNamespace(
        success=1,
        primary=2,
        secondary=2,
        danger=3,
        green=1,
        grey=2,
        blurple=2,
        red=3,
    )
    discord_mod.Color = SimpleNamespace(
        orange=lambda: 1,
        green=lambda: 2,
        blue=lambda: 3,
        red=lambda: 4,
        purple=lambda: 5,
    )
    discord_mod.Interaction = object
    discord_mod.Embed = MagicMock
    discord_mod.app_commands = SimpleNamespace(
        describe=lambda **kwargs: (lambda fn: fn),
        choices=lambda **kwargs: (lambda fn: fn),
        Choice=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    ext_mod = MagicMock()
    commands_mod = MagicMock()
    commands_mod.Bot = MagicMock
    ext_mod.commands = commands_mod

    sys.modules.setdefault("discord", discord_mod)
    sys.modules.setdefault("discord.ext", ext_mod)
    sys.modules.setdefault("discord.ext.commands", commands_mod)


_ensure_discord_mock()

import plugins.platforms.discord.adapter as discord_platform  # noqa: E402
from plugins.platforms.discord.adapter import DiscordAdapter  # noqa: E402


CLAWRY_ROLE_ID = "1511445676666523723"
CLAWRY_USER_ID = "1511441604035088585"


class FakeTextChannel:
    def __init__(self, channel_id: int = 1506747928541397032):
        self.id = channel_id
        self.name = "agent-command-center"
        self.guild = SimpleNamespace(id=1506745934921334804, name="Suneet Command Center")
        self.topic = None


def _message(content: str):
    return SimpleNamespace(
        id=123,
        content=content,
        mentions=[],
        role_mentions=[],
        attachments=[],
        message_snapshots=[],
        reference=None,
        type=discord_platform.discord.MessageType.default,
        created_at=datetime.now(timezone.utc),
        channel=FakeTextChannel(),
        guild=SimpleNamespace(id=1506745934921334804),
        author=SimpleNamespace(
            id=1055171971282899095,
            display_name="suneetagarwal",
            name="suneetagarwal",
            bot=False,
        ),
    )


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(discord_platform.discord, "DMChannel", type("DMChannel", (), {}), raising=False)
    monkeypatch.setattr(discord_platform.discord, "Thread", type("Thread", (), {}), raising=False)
    monkeypatch.setenv("DISCORD_REQUIRE_MENTION", "false")
    monkeypatch.setenv("DISCORD_AUTO_THREAD", "false")
    monkeypatch.delenv("DISCORD_IGNORED_CHANNELS", raising=False)
    monkeypatch.delenv("DISCORD_ALLOWED_CHANNELS", raising=False)
    monkeypatch.delenv("DISCORD_FREE_RESPONSE_CHANNELS", raising=False)

    config = PlatformConfig(
        enabled=True,
        token="fake-token",
        extra={
            "mention_aliases": {
                CLAWRY_ROLE_ID: "Clawry",
                CLAWRY_USER_ID: "Clawry",
            }
        },
    )
    instance = DiscordAdapter(config)
    instance._client = SimpleNamespace(user=SimpleNamespace(id=999))
    instance._text_batch_delay_seconds = 0
    instance.handle_message = AsyncMock()
    return instance


async def _dispatch_text(adapter, raw: str) -> str:
    accepted = await adapter._handle_message(_message(raw))
    assert accepted is True
    adapter.handle_message.assert_awaited_once()
    return adapter.handle_message.await_args.args[0].text


def test_raw_clawry_role_tag_reaches_dispatch_as_canonical_identity(adapter):
    assert asyncio.run(_dispatch_text(adapter, f"<@&{CLAWRY_ROLE_ID}>")) == "@Clawry"


def test_raw_clawry_user_tag_remains_canonical_identity(adapter):
    assert asyncio.run(_dispatch_text(adapter, f"<@{CLAWRY_USER_ID}>")) == "@Clawry"


def test_mixed_clawry_reference_and_task_reaches_dispatch_normalized(adapter):
    raw = f"<@&{CLAWRY_ROLE_ID}> please summarize the actual task request"
    assert asyncio.run(_dispatch_text(adapter, raw)) == "@Clawry please summarize the actual task request"


def test_yaml_mention_aliases_reach_platform_config_extra():
    aliases = {
        CLAWRY_ROLE_ID: "Clawry",
        CLAWRY_USER_ID: "Clawry",
    }
    seeded = discord_platform._apply_yaml_config(
        {"discord": {"mention_aliases": aliases}},
        {"mention_aliases": aliases},
    )
    assert seeded["mention_aliases"] == aliases
