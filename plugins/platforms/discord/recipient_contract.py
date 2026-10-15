"""Explicit action/result recipient contract; validate BEFORE any delivery/splitting.

Producers serialize intent as [[discord-recipients:["Jeff"]]] followed by prose.
Aliases are bound only by the configured verified roster. No prose intent inference.
The declaration travels with persisted final/background output, not session globals.
"""
import json
import re

from .mention_rewriter import _load_alias_map, rewrite_plain_mentions

PREFIX = "[[discord-recipients:"
_DECLARATION = re.compile(r'^\[\[discord-recipients:(\[[^\n]*?\])\]\]\s*')


def prepare_discord_message(text, extra, allowed, *, require_contract=False):
    extra = extra or {}
    if not text:
        return text
    enabled = extra.get("action_mentions") is True
    if enabled and require_contract and extra.get("recipient_contract") is True and not text.startswith(PREFIX):
        raise ValueError("Missing Discord recipient contract: declare verified action/result recipients, or [] for ordinary conversation")
    if enabled and PREFIX in text:
        match = _DECLARATION.match(text)
        if not match or text.count(PREFIX) != 1:
            raise ValueError("Discord recipient contract must be one complete leading JSON declaration")
        try:
            recipients = json.loads(match[1])
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid Discord recipient contract JSON") from exc
        if not isinstance(recipients, list) or any(not isinstance(r, str) or not r for r in recipients):
            raise ValueError("Discord recipients must be a list of nonempty verified aliases or IDs")
        aliases = _load_alias_map(extra.get("mention_roster_path"))
        ids = set(aliases.values())
        resolved = []
        for recipient in recipients:
            uid = recipient if recipient in ids else aliases.get(recipient.casefold())
            if not uid:
                raise ValueError("Unresolved or ambiguous Discord recipient; bind verified roster identity before delivery")
            if uid not in resolved:
                resolved.append(uid)
        policy = allowed.to_dict() if hasattr(allowed, "to_dict") else (allowed or {})
        permitted = policy.get("users", [])
        if any("users" not in policy.get("parse", []) and uid not in [str(x) for x in permitted] for uid in resolved):
            raise ValueError("Discord recipient notification suppressed by effective allowed_mentions")
        body = text[match.end():]
        if not body.strip():
            raise ValueError("Discord recipient contract requires a nonempty message")
        # An explicit routing header avoids accidentally replacing narrative names,
        # and appears once on the first chunk, even when body contains code/URLs.
        text = (" ".join(f"<@{uid}>" for uid in resolved) + "\n" if resolved else "") + body
    return rewrite_plain_mentions(text, extra.get("mention_roster_path"), actionable=enabled)
