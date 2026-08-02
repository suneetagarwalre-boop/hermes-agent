"""Verification gate — independent review pass for high-risk task classes.

WHY THIS EXISTS
---------------
The kanban `review` lane was only half-wired. The CONSUMER side is live and
always has been: the gateway dispatcher polls `status='review'` every tick
(`kanban_db._dispatch_once_locked`), claims via `claim_review_task`, and spawns
a worker through `_default_spawn`. But nothing in the codebase could ever put a
task INTO that status — every `UPDATE tasks SET status` writes a literal that is
never 'review', the dashboard PATCH endpoint rejects it as an unknown status,
and no CLI command or agent tool exposes it. The lane was a loop polling a set
that could never be non-empty. Workers were told (agent/prompt_builder.py) to
use `kanban_block(reason="review-required: ...")` instead, which is a human
escalation hatch, not an automated gate.

This module is the missing producer side. It intercepts completion: when a
high-risk task tries to go `running -> done`, it goes `running -> review`
instead, reassigned to a DIFFERENT profile. The existing dispatcher then picks
it up on its next tick and spawns the reviewer through the existing spawn path.
No new permanent agent, no new daemon, no schema migration.

INDEPENDENCE
------------
`claim_review_task` copies `tasks.assignee` into the reviewer's run, so whoever
owns the task at review time IS the reviewer. Rather than patch that function,
the gate reassigns `tasks.assignee` to the routed reviewer on the way in — the
author never reviews their own work. The original assignee is preserved in the
`review_gate_engaged` event payload and restored when the review completes.

RE-ENTRY
--------
"Has this already been reviewed?" is answered by the presence of a
`review_gate_engaged` event on the task. That keeps the whole mechanism in the
existing audit trail with no new column, and makes rollback a matter of
deleting this file plus one call site.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Optional, Tuple

GATE_EVENT = "review_gate_engaged"
GATE_CLEARED_EVENT = "review_gate_cleared"

# --- risk classes ---------------------------------------------------------- #
# Deterministic keyword match, same philosophy as fleet_health: a regex, not a
# judgment call. Ordered — first match wins, most destructive first.
RISK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("destructive_ops", re.compile(
        r"\b(delete|drop|truncate|purge|wipe|destroy|rm -rf|force[- ]push|"
        r"revoke|deprovision|tear ?down|uninstall|archive all|bulk delete)\b", re.I)),
    ("bulk_crm", re.compile(
        r"\b(bulk|mass|batch|all contacts|every contact|all leads|import|"
        r"merge duplicates|dedupe)\b.{0,40}\b(crm|ghl|follow ?up ?boss|fub|"
        r"contact|lead|subscriber)\b"
        r"|\b(crm|ghl|follow ?up ?boss|fub)\b.{0,40}\b(bulk|mass|batch|import|"
        r"update all|tag all)\b", re.I)),
    ("outbound_comms", re.compile(
        r"\b(send|publish|post|blast|schedule)\b.{0,40}"
        r"\b(email|newsletter|campaign|announcement|press|client|customer|"
        r"投资|investor|board|exec)\b"
        r"|\b(outbound|cold ?email|email sequence|newsletter)\b", re.I)),
    ("engineering", re.compile(
        r"\b(patch|refactor|implement|fix|bug|deploy|migrat\w+|commit|"
        r"pull request|\bPR\b|merge|code ?change|schema|rewrite|hotfix|"
        r"config change)\b", re.I)),
    ("research", re.compile(
        r"\b(research|analy[sz]\w+|investigat\w+|audit|assess\w*|"
        r"recommend\w*|conclusion|findings|due diligence|evaluate)\b", re.I)),
]

# Reviewer routing. Every value MUST be an existing profile — this gate creates
# no new agents. Order in the fallback list matters: first entry that is not the
# author wins, so independence is guaranteed even when the author IS the
# natural reviewer for that class.
REVIEWER_ROUTES: dict[str, list[str]] = {
    "engineering":     ["stark", "dave", "martin"],
    "research":        ["dave", "martin", "stark"],
    "outbound_comms":  ["katt", "dave", "martin"],
    "destructive_ops": ["stark", "dave", "martin"],
    "bulk_crm":        ["martin", "dave", "stark"],
}
DEFAULT_ROUTE = ["dave", "martin", "stark"]

# Explicit overrides an author (or Suneet) can put in the task body.
_FORCE_RE = re.compile(r"\[risk:\s*([a-z_]+)\s*\]", re.I)


def classify(title: str, body: str) -> Optional[str]:
    """Return the risk class for a task, or None if it needs no review.

    An explicit ``[risk:none]`` marker opts out; ``[risk:engineering]`` (or any
    known class) opts in and overrides the keyword scan.
    """
    blob = f"{title or ''}\n{body or ''}"
    forced = _FORCE_RE.search(blob)
    if forced:
        val = forced.group(1).lower()
        if val in ("none", "low", "skip"):
            return None
        if val in REVIEWER_ROUTES:
            return val
    for name, pat in RISK_PATTERNS:
        if pat.search(blob):
            return name
    return None


def pick_reviewer(risk_class: str, author: Optional[str]) -> Optional[str]:
    """First routed profile that is not the author. Independence is the point."""
    for cand in REVIEWER_ROUTES.get(risk_class, DEFAULT_ROUTE) + DEFAULT_ROUTE:
        if cand and cand != (author or ""):
            return cand
    return None


def already_gated(conn: sqlite3.Connection, task_id: str) -> bool:
    """True once this task has been through the gate — the reviewer's own
    completion must not bounce back into review forever."""
    row = conn.execute(
        "SELECT 1 FROM task_events WHERE task_id = ? AND kind = ? LIMIT 1",
        (task_id, GATE_EVENT),
    ).fetchone()
    return row is not None


def gate_info(conn: sqlite3.Connection, task_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = ? "
        "ORDER BY id DESC LIMIT 1", (task_id, GATE_EVENT),
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def evaluate(conn: sqlite3.Connection, task_id: str) -> Tuple[bool, Optional[dict]]:
    """Decide whether `task_id` must be routed to review instead of done.

    Returns ``(needs_review, plan)``. ``plan`` carries risk_class, the original
    assignee and the chosen reviewer. Pure read — the caller performs the write
    inside its own transaction.
    """
    row = conn.execute(
        "SELECT title, body, assignee, status FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if not row:
        return False, None
    title, body, assignee, status = row[0], row[1], row[2], row[3]
    # Only gate a genuine in-flight completion. A manual `hermes kanban complete`
    # on a ready/blocked card is an operator action and is left alone.
    if status != "running":
        return False, None
    if already_gated(conn, task_id):
        return False, None
    risk_class = classify(title or "", body or "")
    if not risk_class:
        return False, None
    reviewer = pick_reviewer(risk_class, assignee)
    if not reviewer:
        return False, None
    return True, {
        "risk_class": risk_class,
        "original_assignee": assignee,
        "reviewer": reviewer,
    }
