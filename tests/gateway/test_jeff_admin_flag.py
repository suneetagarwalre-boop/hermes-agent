"""discord.jeff_admin lets Jeff skip the V3 sandbox (Suneet, 2026-10-24); default stays restricted."""
from types import SimpleNamespace

from gateway.reside_scope import HOME, JEFF
from plugins.platforms.discord.adapter import DiscordAdapter

check = DiscordAdapter._is_reside_restricted_sender


def _adapter(extra):
    return SimpleNamespace(config=SimpleNamespace(extra=extra), _reside_profile_home=HOME)


def test_jeff_restricted_by_default():
    assert check(_adapter({}), JEFF) is True
    assert check(_adapter({"jeff_admin": False}), JEFF) is True


def test_jeff_admin_flag_lifts_sandbox():
    assert check(_adapter({"jeff_admin": True}), JEFF) is False


def test_truthy_strings_do_not_count():
    assert check(_adapter({"jeff_admin": "true"}), JEFF) is True


def test_other_users_never_restricted():
    assert check(_adapter({}), "1055171971282899095") is False
