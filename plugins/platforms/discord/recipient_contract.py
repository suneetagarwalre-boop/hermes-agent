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


class RecipientContractError(ValueError):
    """A logical message was withheld before recipient notification/delivery."""


def declare_discord_recipients(text, recipients):
    """Serialize trusted producer routing, not names inferred from arbitrary prose."""
    if PREFIX in text:
        raise RecipientContractError("Nested Discord recipient declaration in producer body")
    return PREFIX + json.dumps(recipients) + "]]\n" + text


def _names_anyone(text, extra):
    """True unless the text provably names nobody: no mention token, no roster alias or ID.
    An unreadable/empty roster cannot prove that, so it counts as naming someone."""
    if "<@" in text or "@everyone" in text or "@here" in text:
        return True
    aliases = _load_alias_map((extra or {}).get("mention_roster_path"))
    if not aliases:
        return True
    folded = text.casefold()
    return any(uid in text for uid in set(aliases.values())) or any(
        re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", folded) for alias in aliases)


def repair_missing_declaration(text, extra, trusted_recipients=None):
    """Single bounded repair of an UNDECLARED final message; returns ``(text, how)``.

    ``trusted_recipients`` is persisted producer routing (e.g. a cron job's declared
    ``discord_recipients``), never prose. Without it, ``[]`` is declared only when the text
    names no roster member, so no intended notification can be lost. Anything else is returned
    unchanged and ``prepare_discord_message`` still fails closed; malformed or nested
    declarations are never repaired.
    """
    extra = extra or {}
    if (not text or PREFIX in text or extra.get("action_mentions") is not True
            or extra.get("recipient_contract") is not True):
        return text, None
    if trusted_recipients is not None:
        if isinstance(trusted_recipients, list) and all(isinstance(r, str) and r for r in trusted_recipients):
            return declare_discord_recipients(text, list(trusted_recipients)), "trusted_context"
        return text, None
    if not _names_anyone(text, extra):
        return declare_discord_recipients(text, []), "no_recipient_named"
    return text, None


def prepare_discord_message(text, extra, allowed, *, require_contract=False):
    extra = extra or {}
    if not text:
        return text
    enabled = extra.get("action_mentions") is True
    if enabled and require_contract and extra.get("recipient_contract") is True and not text.startswith(PREFIX):
        raise RecipientContractError("Missing Discord recipient contract: declare verified action/result recipients, or [] for ordinary conversation")
    if enabled and PREFIX in text:
        match = _DECLARATION.match(text)
        if not match or text.count(PREFIX) != 1:
            raise RecipientContractError("Discord recipient contract must be one complete leading JSON declaration")
        try:
            recipients = json.loads(match[1])
        except (ValueError, TypeError) as exc:
            raise RecipientContractError("Invalid Discord recipient contract JSON") from exc
        if not isinstance(recipients, list) or any(not isinstance(r, str) or not r for r in recipients):
            raise RecipientContractError("Discord recipients must be a list of nonempty verified aliases or IDs")
        aliases = _load_alias_map(extra.get("mention_roster_path"))
        ids = set(aliases.values())
        resolved = []
        for recipient in recipients:
            uid = recipient if recipient in ids else aliases.get(recipient.casefold())
            if not uid or not re.fullmatch(r"[1-9][0-9]{16,19}", uid):
                raise RecipientContractError("Unresolved or ambiguous Discord recipient; bind verified roster identity before delivery")
            if uid not in resolved:
                resolved.append(uid)
        policy = allowed.to_dict() if hasattr(allowed, "to_dict") else (allowed or {})
        permitted = policy.get("users", [])
        if any("users" not in policy.get("parse", []) and uid not in [str(x) for x in permitted] for uid in resolved):
            raise RecipientContractError("Discord recipient notification suppressed by effective allowed_mentions")
        body = text[match.end():]
        if not body.strip():
            raise RecipientContractError("Discord recipient contract requires a nonempty message")
        # Only the header may notify. Preserve body references as inert text so
        # repeated/chunked references cannot notify a second time.
        for uid in resolved:
            body = re.sub(r'<@!?' + re.escape(uid) + r'>', '@' + uid, body)
        # An explicit routing header avoids accidentally replacing narrative names,
        # and appears once on the first chunk, even when body contains code/URLs.
        return (" ".join(f"<@{uid}>" for uid in resolved) + "\n" if resolved else "") + body
    return rewrite_plain_mentions(text, extra.get("mention_roster_path"), actionable=enabled)


def recipient_mention_policy(text, extra, allowed):
    """Intersect effective permissions with declared intent; never widen suppression.

    Return None for uncontracted/other-profile messages. The returned explicit
    policy must travel with already-prepared chunks; do not infer intent from body.
    """
    if not (extra or {}).get("action_mentions") is True or not text.startswith(PREFIX):
        return None
    prepare_discord_message(text, extra, allowed)
    match = _DECLARATION.match(text)
    assert match is not None  # validated above
    recipients = json.loads(match[1])
    aliases = _load_alias_map(extra.get("mention_roster_path"))
    ids = set(aliases.values())
    resolved = list(dict.fromkeys(r if r in ids else aliases[r.casefold()] for r in recipients))
    return {"parse": [], "users": resolved, "replied_user": False}
