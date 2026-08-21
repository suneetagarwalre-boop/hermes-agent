import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.discord import mention_rewriter
from plugins.platforms.discord.adapter import DiscordAdapter


@pytest.fixture
def roster(tmp_path):
    path = tmp_path / "roster.json"
    path.write_text(json.dumps({
        "members": [
            {"id": "1511441029314908171", "aliases": ["Letterman"]},
            {"id": "1507361939041550517", "aliases": ["Pryor"]},
        ]
    }))
    mention_rewriter._ROSTER_CACHE.clear()
    return path


def test_rewrites_only_prose_mentions(roster):
    text = (
        "Ask @Letterman and @pryor. Keep `@Letterman`, "
        "```txt\n@Pryor\n```, <@1511441029314908171>, "
        "foo@Letterman.com, and \\@Pryor unchanged."
    )
    assert mention_rewriter.rewrite_plain_mentions(text, roster) == (
        "Ask <@1511441029314908171> and <@1507361939041550517>. "
        "Keep `@Letterman`, ```txt\n@Pryor\n```, <@1511441029314908171>, "
        "foo@Letterman.com, and \\@Pryor unchanged."
    )


def test_urls_malformed_rosters_and_ambiguous_aliases_fail_closed(roster):
    assert mention_rewriter.rewrite_plain_mentions(
        "https://example.com/@Letterman ask @Letterman", roster
    ) == "https://example.com/@Letterman ask <@1511441029314908171>"

    roster.write_text(json.dumps({"members": "not-a-list"}))
    mention_rewriter._ROSTER_CACHE.clear()
    assert mention_rewriter.rewrite_plain_mentions("@Letterman", roster) == "@Letterman"

    roster.write_text(json.dumps({
        "members": [
            {"id": "1", "aliases": ["Same"]},
            {"id": "2", "aliases": ["Same"]},
            {"id": "3", "aliases": None},
            "bad-member",
        ]
    }))
    mention_rewriter._ROSTER_CACHE.clear()
    assert mention_rewriter.rewrite_plain_mentions("@Same", roster) == "@Same"


def test_adapter_real_send_path_rewrites_before_channel_send(
    monkeypatch, roster, tmp_path
):
    async def exercise():
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        monkeypatch.setattr(mention_rewriter, "DEFAULT_ROSTER_PATH", roster)
        mention_rewriter._ROSTER_CACHE.clear()

        sent = SimpleNamespace(id=777)
        channel = SimpleNamespace(send=AsyncMock(return_value=sent))
        adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
        adapter._client = SimpleNamespace(
            get_channel=lambda _channel_id: channel,
            fetch_channel=AsyncMock(),
        )
        monkeypatch.setattr(adapter, "_is_forum_parent", lambda channel: False)

        result = await adapter.send("555", "please ask @Letterman")
        return result, channel

    result, channel = asyncio.run(exercise())

    assert result.success is True
    assert channel.send.await_args.kwargs["content"] == (
        "please ask <@1511441029314908171>"
    )
