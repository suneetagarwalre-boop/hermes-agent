"""Per-profile isolation of Discord/Telegram allow/deny gates (issue #72348).

Under ``gateway.multiplex_profiles: true`` every adapter must enforce ITS OWN
profile's allow/deny lists. The historical bugs:

1. First-writer-wins YAML→env bridge: the first profile's
   ``_apply_yaml_config`` wrote ``DISCORD_ALLOWED_CHANNELS`` (etc.) into the
   process-global ``os.environ``; later profiles' values were dropped.
2. Inbound gates read ``os.getenv`` directly, so every adapter enforced the
   FIRST profile's channel/user/role allowlists.
3. Allow-all flags (``DISCORD_ALLOW_ALL_USERS`` / ``GATEWAY_ALLOW_ALL_USERS``)
   read from process env: profile A opting in to open access opened profile B.
4. ``_resolve_allowed_usernames`` unconditionally rewrote
   ``os.environ["DISCORD_ALLOWED_USERS"]`` at runtime.

These tests build two adapter instances with different gate snapshots/extras
and assert each enforces only its own lists, order-independently.
"""

import os
from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter, _GATE_ENV_KEYS


GATE_VARS = [
    "DISCORD_ALLOWED_CHANNELS",
    "DISCORD_IGNORED_CHANNELS",
    "DISCORD_ALLOWED_USERS",
    "DISCORD_ALLOWED_ROLES",
    "DISCORD_ALLOW_ALL_USERS",
    "GATEWAY_ALLOW_ALL_USERS",
    "GATEWAY_ALLOWED_USERS",
    "DISCORD_NO_THREAD_CHANNELS",
    "DISCORD_FREE_RESPONSE_CHANNELS",
    "DISCORD_ALLOW_BOTS",
    "DISCORD_ALLOW_BOTS_CHANNELS",
    "DISCORD_ALLOW_BOTS_IDS",
    "DISCORD_BOT_LOOP_MAX_HOPS",
    "DISCORD_BOT_LOOP_WINDOW_SECONDS",
]


@pytest.fixture(autouse=True)
def _clean_gate_env(monkeypatch):
    for var in GATE_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    # monkeypatch.delenv on an ABSENT var records nothing, so env writes made
    # during the test (e.g. _apply_yaml_config's legacy bridge) would leak
    # into later test modules. Scrub explicitly.
    for var in GATE_VARS:
        os.environ.pop(var, None)


def _adapter(extra: dict | None = None) -> DiscordAdapter:
    adapter = object.__new__(DiscordAdapter)
    adapter.platform = Platform.DISCORD
    adapter.config = PlatformConfig(enabled=True, token="x", extra=dict(extra or {}))
    adapter._gate_env_snapshot = None
    adapter._allowed_user_ids = set()
    adapter._allowed_role_ids = set()
    return adapter


def _snapshot(adapter: DiscordAdapter, values: dict) -> None:
    """Simulate the connect()-time per-profile snapshot."""
    adapter._gate_env_snapshot = {key: values.get(key, "") for key in _GATE_ENV_KEYS}


def _bot_message(*, channel_id: str = "111", author_id: str = "9001"):
    channel = SimpleNamespace(id=int(channel_id), name="team-chat", parent_id=None)
    return SimpleNamespace(
        id=1,
        author=SimpleNamespace(id=int(author_id), bot=True),
        channel=channel,
    )


class TestDiscordBotLoopGuard:
    """The adapter, not either model, must bound bot-to-bot exchanges."""

    @staticmethod
    def _guarded_adapter(**overrides) -> DiscordAdapter:
        adapter = _adapter()
        values = {
            "DISCORD_ALLOW_BOTS_CHANNELS": "111",
            "DISCORD_ALLOW_BOTS_IDS": "9001,9002",
            "DISCORD_BOT_LOOP_MAX_HOPS": "4",
            "DISCORD_BOT_LOOP_WINDOW_SECONDS": "60",
        }
        values.update(overrides)
        _snapshot(adapter, values)
        return adapter

    def test_counter_blocks_after_configured_hop_cap(self):
        adapter = self._guarded_adapter()
        message = _bot_message()

        assert [adapter._bot_loop_guard_ok(message, now=float(i)) for i in range(3)] == [
            True,
            True,
            True,
        ]
        assert adapter._bot_loop_guard_ok(message, now=3.0) is False

    def test_window_expiry_releases_old_hops(self):
        adapter = self._guarded_adapter()
        message = _bot_message()

        for instant in (0.0, 1.0, 2.0):
            assert adapter._bot_loop_guard_ok(message, now=instant) is True
        assert adapter._bot_loop_guard_ok(message, now=59.9) is False
        assert adapter._bot_loop_guard_ok(message, now=60.0) is True

    def test_counter_is_scoped_per_channel(self):
        adapter = self._guarded_adapter(DISCORD_ALLOW_BOTS_CHANNELS="111,222")
        for instant in range(3):
            assert adapter._bot_loop_guard_ok(
                _bot_message(channel_id="111"), now=float(instant)
            ) is True

        assert adapter._bot_loop_guard_ok(
            _bot_message(channel_id="111"), now=3.0
        ) is False
        assert adapter._bot_loop_guard_ok(
            _bot_message(channel_id="222"), now=3.0
        ) is True

    def test_unlisted_channel_is_rejected(self):
        adapter = self._guarded_adapter()
        assert adapter._bot_loop_guard_ok(
            _bot_message(channel_id="222"), now=0.0
        ) is False

    def test_unlisted_bot_id_is_rejected(self):
        adapter = self._guarded_adapter()
        assert adapter._bot_loop_guard_ok(
            _bot_message(author_id="9999"), now=0.0
        ) is False

    def test_peer_only_allowlists_still_count_both_sides_of_two_bot_chain(self):
        adapter_a = self._guarded_adapter(DISCORD_ALLOW_BOTS_IDS="9002")
        adapter_b = self._guarded_adapter(DISCORD_ALLOW_BOTS_IDS="9001")
        from_a = _bot_message(author_id="9001")
        from_b = _bot_message(author_id="9002")

        # Sender reservations happen before Discord can expose the message to
        # the peer. No self MESSAGE_CREATE event is needed for synchronization.
        for hop, (sender, receiver, message) in enumerate(
            (
                (adapter_a, adapter_b, from_a),
                (adapter_b, adapter_a, from_b),
                (adapter_a, adapter_b, from_a),
            ),
            start=1,
        ):
            target_id = "9002" if sender is adapter_a else "9001"
            assert sender._bot_loop_outbound_guard_ok(
                message.channel,
                f"<@{target_id}> continue",
                now=float(hop),
            ) is True
            assert receiver._bot_loop_guard_ok(message, now=float(hop)) is True

        assert adapter_b._bot_loop_outbound_guard_ok(
            from_b.channel,
            "<@9001> hop four",
            now=4.0,
        ) is False

    @pytest.mark.asyncio
    async def test_send_refuses_cap_hop_before_discord_network_write(
        self, monkeypatch, caplog
    ):
        from unittest.mock import AsyncMock
        from plugins.platforms.discord import adapter as discord_adapter_module

        adapter = self._guarded_adapter(DISCORD_ALLOW_BOTS_IDS="9002")
        channel = SimpleNamespace(
            id=111,
            name="team-chat",
            parent_id=None,
            send=AsyncMock(),
        )
        adapter._client = SimpleNamespace(
            user=SimpleNamespace(id=9001, bot=True),
            get_channel=lambda _channel_id: channel,
        )
        setattr(adapter, "_record_discord_response", lambda **_kwargs: None)

        for hop in (1.0, 2.0, 3.0):
            assert adapter._bot_loop_outbound_guard_ok(
                channel,
                "<@9002> continue",
                now=hop,
            ) is True
        monkeypatch.setattr(discord_adapter_module.time, "monotonic", lambda: 4.0)

        result = await adapter.send("111", "<@9002> hop four")

        assert result.success is False
        assert result.error == "Discord bot loop guard refused inter-agent send"
        channel.send.assert_not_awaited()
        assert "direction=outbound" in caplog.text
        assert "reason=hop_cap hop=4 max_hops=4" in caplog.text

    def test_missing_counter_config_preserves_legacy_allow_bots_behavior(self):
        adapter = self._guarded_adapter(
            DISCORD_BOT_LOOP_MAX_HOPS="",
            DISCORD_BOT_LOOP_WINDOW_SECONDS="",
        )
        message = _bot_message()

        assert all(
            adapter._bot_loop_guard_ok(message, now=float(i))
            for i in range(20)
        )

    @pytest.mark.parametrize(
        "overrides",
        (
            {"DISCORD_BOT_LOOP_MAX_HOPS": ""},
            {"DISCORD_BOT_LOOP_WINDOW_SECONDS": ""},
            {"DISCORD_BOT_LOOP_MAX_HOPS": "0"},
            {"DISCORD_BOT_LOOP_WINDOW_SECONDS": "0"},
        ),
    )
    def test_partial_or_nonpositive_counter_config_disables_counter(self, overrides):
        adapter = self._guarded_adapter(**overrides)
        message = _bot_message()

        assert all(
            adapter._bot_loop_guard_ok(message, now=float(i))
            for i in range(20)
        )

    def test_message_admission_refuses_the_cap_hop_before_model_dispatch(
        self, monkeypatch, caplog
    ):
        import discord
        from plugins.platforms.discord import adapter as discord_adapter_module

        adapter = self._guarded_adapter(DISCORD_ALLOW_BOTS="mentions")
        self_user = SimpleNamespace(id=9002, bot=True)
        adapter._client = SimpleNamespace(user=self_user)
        setattr(
            adapter,
            "_dedup",
            SimpleNamespace(
                is_duplicate=lambda _message_id: False,
                contains=lambda _message_id: False,
            ),
        )
        message = _bot_message(author_id="9001")
        message.type = discord.MessageType.default
        message.content = "<@9002> continue the pressure-test chain"
        message.mentions = [self_user]

        clock = iter((0.0, 1.0, 2.0, 3.0))
        monkeypatch.setattr(discord_adapter_module.time, "monotonic", lambda: next(clock))

        admitted = []
        for message_id in range(1, 5):
            message.id = message_id
            admitted.append(adapter._discord_message_admission(message, claim=True)[0])

        assert admitted == [True, True, True, False]
        assert "reason=hop_cap hop=4 max_hops=4" in caplog.text


class TestTwoAdapterChannelIsolation:
    """Two adapters with different allowed_channels enforce their OWN lists."""

    def test_snapshots_isolate_allowed_channels(self):
        a = _adapter()
        b = _adapter()
        _snapshot(a, {"DISCORD_ALLOWED_CHANNELS": "111"})
        _snapshot(b, {"DISCORD_ALLOWED_CHANNELS": "222"})

        assert a._get_allowed_channels() == {"111"}
        assert b._get_allowed_channels() == {"222"}

    def test_order_independent(self):
        # Reverse construction order — the winner must not change.
        b = _adapter()
        _snapshot(b, {"DISCORD_ALLOWED_CHANNELS": "222"})
        a = _adapter()
        _snapshot(a, {"DISCORD_ALLOWED_CHANNELS": "111"})

        assert a._discord_channel_ids_allowed({"111"}) is True
        assert a._discord_channel_ids_allowed({"222"}) is False
        assert b._discord_channel_ids_allowed({"222"}) is True
        assert b._discord_channel_ids_allowed({"111"}) is False

    def test_extras_isolate_allowed_channels_without_snapshot(self):
        """Config-extra seeding isolates gates even before connect()."""
        a = _adapter({"allowed_channels": "111"})
        b = _adapter({"allowed_channels": "222"})

        assert a._get_allowed_channels() == {"111"}
        assert b._get_allowed_channels() == {"222"}

    def test_process_env_does_not_leak_into_snapshotted_adapter(self, monkeypatch):
        """A first-writer process-global env value must not override a
        snapshotted adapter's own (empty) gate."""
        monkeypatch.setenv("DISCORD_ALLOWED_CHANNELS", "999")
        b = _adapter({"allowed_channels": "222"})
        _snapshot(b, {"DISCORD_ALLOWED_CHANNELS": "222"})
        assert b._get_allowed_channels() == {"222"}

    def test_ignored_channels_isolated(self):
        a = _adapter()
        b = _adapter()
        _snapshot(a, {"DISCORD_IGNORED_CHANNELS": "311"})
        _snapshot(b, {"DISCORD_IGNORED_CHANNELS": "322"})
        assert a._get_ignored_channels() == {"311"}
        assert b._get_ignored_channels() == {"322"}


class TestTwoAdapterUserRoleIsolation:
    def test_allowed_users_isolated(self):
        a = _adapter()
        b = _adapter()
        _snapshot(a, {"DISCORD_ALLOWED_USERS": "1001,<@1002>"})
        _snapshot(b, {"DISCORD_ALLOWED_USERS": "2001"})
        assert a._get_allowed_users() == {"1001", "1002"}
        assert b._get_allowed_users() == {"2001"}

    def test_allowed_roles_isolated(self):
        a = _adapter()
        b = _adapter()
        _snapshot(a, {"DISCORD_ALLOWED_ROLES": "31,32"})
        _snapshot(b, {"DISCORD_ALLOWED_ROLES": "41"})
        assert a._get_allowed_roles() == {31, 32}
        assert b._get_allowed_roles() == {41}

    def test_is_allowed_user_enforces_own_list(self, monkeypatch):
        # Pairing store must not interfere.
        monkeypatch.setattr(
            DiscordAdapter, "_is_pairing_approved_user", lambda self, uid: False
        )
        a = _adapter()
        b = _adapter()
        _snapshot(a, {"DISCORD_ALLOWED_USERS": "1001"})
        _snapshot(b, {"DISCORD_ALLOWED_USERS": "2001"})
        a._allowed_user_ids = a._get_allowed_users()
        b._allowed_user_ids = b._get_allowed_users()

        assert a._is_allowed_user("1001") is True
        assert a._is_allowed_user("2001") is False
        assert b._is_allowed_user("2001") is True
        assert b._is_allowed_user("1001") is False


class TestAllowAllFlagIsolation:
    """Profile A's allow-all flag must never authorize profile B (negative case)."""

    def test_discord_allow_all_isolated(self, monkeypatch):
        monkeypatch.setattr(
            DiscordAdapter, "_is_pairing_approved_user", lambda self, uid: False
        )
        open_profile = _adapter()
        closed_profile = _adapter()
        _snapshot(open_profile, {"DISCORD_ALLOW_ALL_USERS": "true"})
        _snapshot(closed_profile, {})

        assert open_profile._is_allowed_user("555") is True
        assert closed_profile._is_allowed_user("555") is False

    def test_env_allow_all_does_not_open_snapshotted_adapter(self, monkeypatch):
        """First-writer env DISCORD_ALLOW_ALL_USERS=true (profile A) must not
        open a snapshotted profile B."""
        monkeypatch.setattr(
            DiscordAdapter, "_is_pairing_approved_user", lambda self, uid: False
        )
        monkeypatch.setenv("DISCORD_ALLOW_ALL_USERS", "true")
        b = _adapter()
        _snapshot(b, {})  # profile B: no allow-all, no allowlists
        assert b._discord_allow_all_users() is False
        assert b._is_allowed_user("555") is False

    def test_gateway_allow_all_isolated(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
        b = _adapter()
        _snapshot(b, {})
        assert b._gateway_allow_all_users() is False


class TestSlashGateIsolation:
    """Slash-command channel gates use per-adapter values too."""

    def test_evaluate_slash_channel_gate_per_adapter(self, monkeypatch):
        import types

        discord_lib = pytest.importorskip(
            "discord", reason="discord.py optional dep not installed"
        )

        a = _adapter()
        b = _adapter()
        _snapshot(a, {"DISCORD_ALLOWED_CHANNELS": "111"})
        _snapshot(b, {"DISCORD_ALLOWED_CHANNELS": "222"})

        def _keys(self, chan, parent):
            return {str(getattr(chan, "id", ""))}

        monkeypatch.setattr(
            DiscordAdapter, "_discord_channel_keys_from_channel", _keys
        )
        monkeypatch.setattr(
            DiscordAdapter, "_get_parent_channel_id", lambda self, c: None
        )

        chan = types.SimpleNamespace(id=111)
        interaction = types.SimpleNamespace(
            channel=chan, channel_id=111, user=types.SimpleNamespace(id=999, roles=[]),
        )
        # channel 111: allowed for A's gate...
        allowed_a, reason_a = a._evaluate_slash_authorization(interaction)
        # ...but B must reject it on ITS channel gate.
        allowed_b, reason_b = b._evaluate_slash_authorization(interaction)
        assert reason_a != "channel not in DISCORD_ALLOWED_CHANNELS"
        assert allowed_b is False
        assert reason_b == "channel not in DISCORD_ALLOWED_CHANNELS"


class TestUsernameResolutionEnvWrite:
    """_resolve_allowed_usernames must not clobber process env under multiplex."""

    @pytest.mark.asyncio
    async def test_no_env_write_when_multiplex_active(self, monkeypatch):
        from agent import secret_scope

        monkeypatch.setenv("DISCORD_ALLOWED_USERS", "999")
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)

        adapter = _adapter()
        _snapshot(adapter, {"DISCORD_ALLOWED_USERS": "teknium"})
        adapter._allowed_user_ids = {"teknium"}

        member = type(
            "M",
            (),
            {
                "id": 12345,
                "name": "teknium",
                "display_name": "teknium",
                "global_name": "teknium",
                "discriminator": "0",
            },
        )()
        guild = type(
            "G", (), {"members": [member], "member_count": 1, "name": "g"},
        )()
        adapter._client = type("C", (), {"guilds": [guild]})()

        await adapter._resolve_allowed_usernames()

        assert adapter._allowed_user_ids == {"12345"}
        # Snapshot updated for this adapter only.
        assert adapter._gate_env_snapshot["DISCORD_ALLOWED_USERS"] == "12345"
        # Process-global env untouched — other profiles unaffected.
        assert os.environ["DISCORD_ALLOWED_USERS"] == "999"

    @pytest.mark.asyncio
    async def test_env_write_preserved_single_profile(self, monkeypatch):
        from agent import secret_scope

        monkeypatch.setenv("DISCORD_ALLOWED_USERS", "teknium")
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)

        adapter = _adapter()
        adapter._allowed_user_ids = {"teknium"}

        member = type(
            "M",
            (),
            {
                "id": 12345,
                "name": "teknium",
                "display_name": "teknium",
                "global_name": "teknium",
                "discriminator": "0",
            },
        )()
        guild = type(
            "G", (), {"members": [member], "member_count": 1, "name": "g"},
        )()
        adapter._client = type("C", (), {"guilds": [guild]})()

        await adapter._resolve_allowed_usernames()

        # Legacy single-profile behavior: env rewritten to resolved IDs.
        assert os.environ["DISCORD_ALLOWED_USERS"] == "12345"


class TestYamlBridgeSeeding:
    """_apply_yaml_config seeds gates into extra and skips env writes when
    loading a profile-scoped config under multiplex."""

    def test_seeds_extra_and_bridges_env_single_profile(self, monkeypatch):
        from agent import secret_scope
        from plugins.platforms.discord.adapter import _apply_yaml_config

        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)
        seeded = _apply_yaml_config(
            {},
            {
                "allowed_channels": ["111", "112"],
                "ignored_channels": "333",
                "allow_from": ["1001"],
                "allowed_roles": [31],
                "allow_all_users": False,
            },
        )
        assert seeded["allowed_channels"] == "111,112"
        assert seeded["ignored_channels"] == "333"
        assert seeded["allow_from"] == "1001"
        assert seeded["allowed_roles"] == "31"
        assert seeded["allow_all_users"] == "false"
        # Legacy env bridge preserved for single-profile deployments.
        assert os.environ["DISCORD_ALLOWED_CHANNELS"] == "111,112"

    def test_profile_scoped_load_skips_env_bridge(self, monkeypatch):
        from agent import secret_scope
        from plugins.platforms.discord.adapter import _apply_yaml_config

        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope({"SOME": "scope"})
        try:
            seeded = _apply_yaml_config(
                {}, {"allowed_channels": "222", "allow_from": "2001"},
            )
        finally:
            secret_scope.reset_secret_scope(token)

        # Gates still seeded per-adapter...
        assert seeded["allowed_channels"] == "222"
        assert seeded["allow_from"] == "2001"
        # ...but process-global env stays clean: no cross-profile leak.
        assert os.getenv("DISCORD_ALLOWED_CHANNELS") is None
        assert os.getenv("DISCORD_ALLOWED_USERS") is None

    def test_first_writer_env_does_not_mask_second_profile_extras(self, monkeypatch):
        """End-to-end shape of the original repro: profile A bridges env first;
        profile B (scoped load) still gets ITS channels via extras."""
        from agent import secret_scope
        from plugins.platforms.discord.adapter import _apply_yaml_config

        # Profile A: single first load (multiplex flag not yet set).
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)
        seeded_a = _apply_yaml_config({}, {"allowed_channels": "111"})
        assert os.environ["DISCORD_ALLOWED_CHANNELS"] == "111"

        # Profile B: scoped load under multiplex.
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope({})
        try:
            seeded_b = _apply_yaml_config({}, {"allowed_channels": "222"})
        finally:
            secret_scope.reset_secret_scope(token)

        a = _adapter(seeded_a)
        b = _adapter(seeded_b)
        # B's snapshot taken inside its (empty-env) profile scope.
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope({})
        try:
            b._snapshot_gate_env()
        finally:
            secret_scope.reset_secret_scope(token)
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)
        a._snapshot_gate_env()

        assert a._get_allowed_channels() == {"111"}
        assert b._get_allowed_channels() == {"222"}


class TestTelegramGateIsolation:
    """Telegram mirror (reported by @yournetworkplug-ctrl in #72348)."""

    def test_scoped_gate_env_prefers_profile_scope(self, monkeypatch):
        from agent import secret_scope
        from plugins.platforms.telegram.adapter import _scoped_gate_env

        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope(
            {"TELEGRAM_ALLOWED_USERS": "222222222"}
        )
        try:
            assert _scoped_gate_env("TELEGRAM_ALLOWED_USERS") == "222222222"
        finally:
            secret_scope.reset_secret_scope(token)

    def test_scoped_gate_env_authoritative_scope_miss(self, monkeypatch):
        """Under multiplex, a scope WITHOUT the key must not fall through to
        another profile's process-env value."""
        from agent import secret_scope
        from plugins.platforms.telegram.adapter import _scoped_gate_env

        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope({})
        try:
            assert _scoped_gate_env("TELEGRAM_ALLOWED_USERS") == ""
        finally:
            secret_scope.reset_secret_scope(token)

    def test_scoped_gate_env_single_profile_fallback(self, monkeypatch):
        from agent import secret_scope
        from plugins.platforms.telegram.adapter import _scoped_gate_env

        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)
        assert _scoped_gate_env("TELEGRAM_ALLOWED_USERS") == "111111111"

    def test_telegram_yaml_bridge_skipped_for_scoped_profile(self, monkeypatch):
        from agent import secret_scope
        from plugins.platforms.telegram.adapter import _apply_yaml_config

        monkeypatch.delenv("TELEGRAM_ALLOWED_CHATS", raising=False)
        monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope({})
        try:
            extras = _apply_yaml_config(
                {}, {"allowed_chats": ["-100200"], "allow_from": "222222222"},
            )
        finally:
            secret_scope.reset_secret_scope(token)

        # allowed_chats reaches PlatformConfig.extra via the shared-key loop
        # in gateway/config.py (type-preserving); _apply_yaml_config must not
        # write either gate into the process-global env for a scoped profile.
        assert extras is None or "allowed_chats" not in extras
        assert os.getenv("TELEGRAM_ALLOWED_CHATS") is None
        assert os.getenv("TELEGRAM_ALLOWED_USERS") is None
