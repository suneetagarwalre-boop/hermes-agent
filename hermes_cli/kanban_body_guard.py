"""Fail-closed validation for Kanban work briefs.

A Kanban body is the worker's job specification. User-facing creation paths
must reject bodies that are missing, placeholders, or too vague to delegate,
and the dispatcher must re-check the same policy so legacy/manual rows cannot
spawn a worker with an incomplete brief.

The accepted compact format is intentionally ordinary Markdown::

    Action: Make the requested change.
    Source: GitHub repo owner/name, issue #123.
    Scope: Include X; exclude Y.
    Acceptance: Run the named check and report its result.
    If absent: Stop and report that the source could not be found.

Headings (``## Action`` followed by text) are accepted as well as inline
``Label: value`` lines. The labels make omissions mechanically detectable and
let validation errors tell the router exactly what to add.
"""

from __future__ import annotations

import re
import sys
from typing import Optional

__all__ = [
    "BlankBodyError",
    "BriefValidationError",
    "PLACEHOLDER_BODIES",
    "is_no_card_status_query",
    "is_placeholder_body",
    "resolve_body",
    "validate_body",
]


class BriefValidationError(ValueError):
    """Raised when a card would be created or dispatched without a usable brief."""

    def __init__(self, message: str, *, missing: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing = missing


# Backward-compatible name used by the first blank-body guard.
BlankBodyError = BriefValidationError


PLACEHOLDER_BODIES = frozenset(
    {
        "-",
        "--",
        "---",
        ".",
        "..",
        "...",
        "n/a",
        "na",
        "n.a.",
        "none",
        "null",
        "nil",
        "tbd",
        "tba",
        "todo",
        "?",
        "??",
        "(none)",
        "(empty)",
        "(no body)",
        "no body",
        "no description",
        "placeholder",
        "unknown",
    }
)

_STDIN_SENTINEL = "-"

# Canonical field name -> accepted ordinary-English labels. Longer aliases are
# sorted first when building the regex so "requested action" wins over "action".
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "requested action": ("requested action", "action", "request"),
    "source/system and exact identifier when known": (
        "source/system",
        "source or system",
        "source",
        "system",
    ),
    "scope, including inclusions and exclusions": (
        "scope/inclusions/exclusions",
        "scope and exclusions",
        "scope",
    ),
    "acceptance check": (
        "acceptance check",
        "acceptance criteria",
        "acceptance",
        "done when",
        "verification",
        "verify",
    ),
    "what to do if the source is absent": (
        "what to do if the source is absent",
        "if source is absent",
        "if source absent",
        "if the source is absent",
        "if absent",
        "source absent",
        "missing source",
    ),
}

_STATUS_QUERY_RE = re.compile(
    r"^(?:(?:what(?:'s|\s+is)\s+the\s+status\s+(?:of|for)|"
    r"status\s+(?:of|for)|show\s+(?:me\s+)?the\s+status\s+(?:of|for))"
    r"\s+(?:(?:task|card|job)\s+)?\S+|"
    r"is\s+(?:task|card|job)\s+\S+\s+(?:done|complete|blocked|running))$",
    re.IGNORECASE,
)


def is_placeholder_body(body: Optional[str]) -> bool:
    """Return True when ``body`` carries no usable instruction."""
    if body is None:
        return True
    stripped = body.strip()
    if not stripped:
        return True
    if stripped.casefold() in PLACEHOLDER_BODIES:
        return True
    if not any(ch.isalnum() for ch in stripped):
        return True
    return False


def is_no_card_status_query(title: Optional[str], body: Optional[str]) -> bool:
    """Return True for a simple read-only status question.

    These requests should be answered synchronously (for example with
    ``kanban_show``), not converted into specialist work. The matcher is
    deliberately narrow so research questions and action requests still pass
    through normal brief validation.
    """
    # A substantive body can turn a status-looking title into real work
    # ("check status, then repair it"). Only title-only/placeholder requests
    # are classified from the title; otherwise the body itself must be the
    # complete, simple status question.
    candidate = body if not is_placeholder_body(body) else title
    if not candidate:
        return False
    return _STATUS_QUERY_RE.fullmatch(str(candidate).strip().rstrip("?.! ")) is not None


def resolve_body(body: Optional[str], *, stdin=None) -> Optional[str]:
    """Turn ``--body -`` into the real multiline body read from stdin."""
    if body is None or body.strip() != _STDIN_SENTINEL:
        return body

    stream = stdin if stdin is not None else sys.stdin
    if stream is None or getattr(stream, "isatty", lambda: False)():
        raise BriefValidationError(
            "--body - means 'read the work brief from stdin', but stdin is a "
            "terminal. Pipe or heredoc the brief, or pass it directly with "
            "--body."
        )
    try:
        piped = stream.read()
    except Exception as exc:  # pragma: no cover - platform stdin failure
        raise BriefValidationError(f"--body -: could not read stdin: {exc}") from exc

    if is_placeholder_body(piped):
        raise BriefValidationError(
            "--body - was given but stdin contained no work brief. Supply the "
            "requested action, source/system, scope, acceptance check, and "
            "what to do if the source is absent."
        )
    return piped


def _label_pattern(label: str) -> str:
    words = [re.escape(part) for part in label.split()]
    return r"[\s_/-]+".join(words)


def _extract_structured_fields(body: str) -> dict[str, str]:
    """Extract accepted inline labels or Markdown headings from ``body``."""
    alias_to_field: list[tuple[str, str]] = []
    for field, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            alias_to_field.append((alias, field))
    alias_to_field.sort(key=lambda pair: len(pair[0]), reverse=True)
    labels = "|".join(_label_pattern(alias) for alias, _ in alias_to_field)
    matcher = re.compile(
        rf"^\s*(?:[-*+]\s*)?(?:#{{1,6}}\s*)?(?P<label>{labels})\s*"
        rf"(?::|—|-)?\s*(?P<value>.*)$",
        re.IGNORECASE,
    )
    alias_lookup = {
        re.sub(r"[\s_/-]+", " ", alias.casefold()).strip(): field
        for alias, field in alias_to_field
    }

    fields: dict[str, str] = {}
    active_field: Optional[str] = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            active_field = None
            continue
        match = matcher.match(line)
        if match:
            normal_label = re.sub(
                r"[\s_/-]+", " ", match.group("label").casefold()
            ).strip()
            active_field = alias_lookup[normal_label]
            value = match.group("value").strip()
            if value:
                fields[active_field] = value
            else:
                fields.setdefault(active_field, "")
            continue
        if active_field and not re.match(r"^(?:#|[-*+]\s+)", line):
            fields[active_field] = (
                f"{fields.get(active_field, '')} {line}".strip()
            )
    return fields


def _missing_structured_fields(body: str) -> tuple[str, ...]:
    fields = _extract_structured_fields(body)
    return tuple(
        field
        for field in _FIELD_ALIASES
        if field not in fields or is_placeholder_body(fields[field])
    )


def validate_body(
    body: Optional[str],
    *,
    require_structured: bool = True,
    title: Optional[str] = None,
    allow_missing: bool = False,
) -> Optional[str]:
    """Return ``body`` when it is dispatchable; otherwise raise clearly.

    ``allow_missing`` remains accepted for API compatibility but intentionally
    does not bypass the fail-closed rule: even triage cards need a real opening
    brief. ``require_structured=False`` is only for non-specialist draft cards;
    dispatched/assigned work must always use the compact five-field format.
    """
    del allow_missing

    if is_no_card_status_query(title, body):
        raise BriefValidationError(
            "This is a simple status question, not delegated work. Answer it "
            "directly from the source (for example with kanban_show); do not "
            "create a Kanban card."
        )

    if is_placeholder_body(body):
        shown = "<missing>" if body is None else repr(body.strip()[:40])
        raise BriefValidationError(
            f"Cannot create or dispatch this card because its work brief is "
            f"{shown}. Add the requested action, source/system and exact "
            "identifier when known, scope/inclusions/exclusions, acceptance "
            "check, and what to do if the source is absent.",
            missing=tuple(_FIELD_ALIASES),
        )

    assert body is not None  # narrowed by is_placeholder_body above
    if require_structured:
        missing = _missing_structured_fields(body)
        if missing:
            raise BriefValidationError(
                "The work brief is incomplete. Add: " + "; ".join(missing) + ".",
                missing=missing,
            )
    return body
