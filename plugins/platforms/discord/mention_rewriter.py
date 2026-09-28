"""Deterministic plain-name to Discord user-mention rewriting.

A separate roster refresh reads the guild member API and writes the shared JSON
file. This module fails closed: no roster, malformed roster, or ambiguous alias
means no rewrite, never a guessed Discord ID.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping

DEFAULT_ROSTER_PATH = Path(
    os.environ.get(
        "DISCORD_MENTION_ROSTER_PATH",
        "~/.config/discord-command-center-roster.json",
    )
).expanduser()

_ROSTER_CACHE: dict[Path, tuple[int, int, dict[str, str]]] = {}
_URL_RE = re.compile(r"https?://[^\s<]+", flags=re.IGNORECASE)


def _load_alias_map(roster_path: str | os.PathLike[str] | None = None) -> Mapping[str, str]:
    path = Path(roster_path).expanduser() if roster_path else DEFAULT_ROSTER_PATH
    try:
        stat = path.stat()
    except OSError:
        return {}
    cached = _ROSTER_CACHE.get(path)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or not isinstance(payload.get("members"), list):
        return {}

    alias_ids: dict[str, set[str]] = {}
    for member in payload["members"]:
        if not isinstance(member, dict):
            continue
        user_id = str(member.get("id", "")).strip()
        member_aliases = member.get("aliases", [])
        if not user_id.isdigit() or not isinstance(member_aliases, list):
            continue
        for alias in member_aliases:
            if not isinstance(alias, str):
                continue
            name = alias.strip()
            if name and "@" not in name and "`" not in name:
                alias_ids.setdefault(name.casefold(), set()).add(user_id)

    aliases = {
        alias: next(iter(user_ids))
        for alias, user_ids in alias_ids.items()
        if len(user_ids) == 1
    }
    _ROSTER_CACHE[path] = (stat.st_mtime_ns, stat.st_size, aliases)
    return aliases


def _rewrite_non_url(text: str, aliases: Mapping[str, str], pattern: re.Pattern[str]) -> str:
    def replacement(match: re.Match[str]) -> str:
        user_id = aliases.get(match.group(1).casefold())
        return f"<@{user_id}>" if user_id else match.group(0)

    return pattern.sub(replacement, text)


def _rewrite_prose(text: str, aliases: Mapping[str, str]) -> str:
    if not text or not aliases or "@" not in text:
        return text
    alternatives = "|".join(
        re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)
    )
    pattern = re.compile(
        rf"(?<![\w<\\])@({alternatives})(?![\w])",
        flags=re.IGNORECASE,
    )
    output: list[str] = []
    cursor = 0
    for match in _URL_RE.finditer(text):
        output.append(_rewrite_non_url(text[cursor:match.start()], aliases, pattern))
        output.append(match.group(0))
        cursor = match.end()
    output.append(_rewrite_non_url(text[cursor:], aliases, pattern))
    return "".join(output)


def rewrite_plain_mentions(
    text: str,
    roster_path: str | os.PathLike[str] | None = None,
) -> str:
    """Rewrite known ``@Name`` tokens outside code spans/fences and URLs."""
    if not isinstance(text, str) or "@" not in text:
        return text
    aliases = _load_alias_map(roster_path)
    if not aliases:
        return text
    output: list[str] = []
    cursor = 0
    length = len(text)
    while cursor < length:
        tick = text.find("`", cursor)
        if tick < 0:
            output.append(_rewrite_prose(text[cursor:], aliases))
            break
        output.append(_rewrite_prose(text[cursor:tick], aliases))
        run_end = tick
        while run_end < length and text[run_end] == "`":
            run_end += 1
        delimiter = text[tick:run_end]
        close = text.find(delimiter, run_end)
        if close < 0:
            output.append(text[tick:])
            break
        close += len(delimiter)
        output.append(text[tick:close])
        cursor = close
    return "".join(output)
