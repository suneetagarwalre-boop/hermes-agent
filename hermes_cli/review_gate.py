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
routed task tries to go `running -> done`, it goes `running -> review` instead.
Risk-bearing work is reassigned to a DIFFERENT profile; Redd-owned content stays
with Redd for its domain revision pass. The existing dispatcher then picks it
up on its next tick and spawns the reviewer through the existing spawn path. No
new permanent agent, no new daemon, no schema migration.

INDEPENDENCE
------------
`claim_review_task` copies `tasks.assignee` into the reviewer's run, so whoever
owns the task at review time IS the reviewer. Rather than patch that function,
the gate sets `tasks.assignee` to the routed domain reviewer on the way in.
Operational risk classes never self-review. Content is the intentional
exception: prose feedback and revision remain with Redd. The original assignee
is preserved in the `review_gate_engaged` event payload and restored when the
review completes.

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
# Classification is domain-first for specialist-owned content, then falls back
# to deterministic risk patterns. Ordered — first match wins, most destructive
# first. Broad operational words never outweigh a clear content brief by
# themselves.
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
# no new agents. Operational classes select the first entry that is not the
# author. Content deliberately stays with Redd because feedback is revision,
# not engineering verification.
REVIEWER_ROUTES: dict[str, list[str]] = {
    "content":         ["redd"],
    "engineering":     ["stark", "dave", "martin"],
    "research":        ["dave", "martin", "stark"],
    "outbound_comms":  ["katt", "dave", "martin"],
    "destructive_ops": ["stark", "dave", "martin"],
    "bulk_crm":        ["martin", "dave", "stark"],
}
DEFAULT_ROUTE = ["dave", "martin", "stark"]

# Explicit overrides an author (or Suneet) can put in the task body.
_FORCE_RE = re.compile(r"\[risk:\s*([a-z_]+)\s*\]", re.I)

# Redd owns content creation and revision. These signals intentionally include
# the ordinary nouns used in briefs (Facebook, copy, voice, Notion/Drive
# content) and do not treat generic words such as system/workflow/build as
# engineering intent.
_CONTENT_ASSIGNEES = frozenset({"redd"})
_CONTENT_DOMAIN_RE = re.compile(
    r"\b(facebook|linkedin|email|newsletter|script|voice|copy|content|post|"
    r"caption|article|blog|draft|rewrite|hook|cta|notion|drive|perry)\b",
    re.I,
)

# A directive is a title/body line whose leading verb asks the worker to act.
# Anchoring here is what distinguishes "Rewrite Python code" from content such
# as "Explain how to rewrite Python code". Common bullets, labels, and polite
# prefixes are accepted so risk cannot be bypassed with "Please" or
# "Go ahead and".
_DIRECTIVE_START = (
    r"(?:^|\n)\s*(?:[-*]\s*)?"
    r"(?:(?:action|requirements?|task|implementation)\s*:\s*)?"
    r"(?:(?:please|kindly|go ahead and|now|then|could you|can you|would you)\s+)?"
)

_TECHNICAL_DIRECTIVE_RE = re.compile(
    _DIRECTIVE_START
    + r"(?:technical implementation\b"
    r"|(?:patch|refactor|debug|rewrite|change|fix|edit|modify|update)\s+"
    r"(?:(?:the|this|that|our|a|an|existing|current|new|hermes)\s+){0,4}"
    r"(?:(?:python|javascript|typescript|bash|shell|sql|git|server|launchd)\s+)?"
    r"(?:(?:source|application)\s+)?(?:code|codebase|repository|repo|"
    r"infrastructure|infra|software|database|api|cli|daemon|server|docker|"
    r"container|kubernetes|launchd|config(?:uration)?|schema|bug|tests?|files?|"
    r"modules?|workflows?|branches?|plist)\b"
    r"|(?:implement|deploy|migrate|install|configure)\s+"
    r"(?:(?:the|this|that|our|a|an|existing|current|new|hermes)\s+){0,4}"
    r"(?:code|codebase|repository|repo|infrastructure|infra|software|database|"
    r"api|service|integration|cli|daemon|server|docker|container|kubernetes|"
    r"launchd|config(?:uration)?|schema|migration|package|application|app)\b"
    r"|(?:write|edit|modify)\s+(?:(?:the|this)\s+)?(?:source\s+)?(?:code|"
    r"codebase|python file|javascript file|typescript file|config(?:uration)? file)\b"
    r"|(?:add|run)\b.{0,30}\b(?:pytest|unit tests?|regression tests?|test suite)\b"
    r"|build\s+(?:(?:the|this|that|our|a|an|existing|current|new|hermes)\s+){0,4}"
    r"(?:app|application|api|service|integration|cli|package|container|website|"
    r"software|code|infrastructure|infra|docker)\b"
    r"|git\s+(?:commit|push)\b"
    r"|push\s+(?:(?:the|this|our|a)\s+)?(?:git\s+)?(?:changes?|branch)\b"
    r"|(?:commit|merge)\s+(?:(?:the|this|our|a)\s+)?(?:git\s+)?(?:changes?|"
    r"branch|pull request)\b"
    r"|create\s+(?:(?:the|this|our|a|an|new)\s+){0,3}(?:git\s+)?branch\b"
    r"|(?:open|create)\b.{0,30}\bpull request\b)",
    re.I | re.M,
)

# Side-effect directives are different from drafting content that merely talks
# about those actions. These command-shaped patterns let operational risk keep
# precedence without letting words in copy ("delete bad habits", "publish when
# ready") hijack every Redd brief.
_DESTRUCTIVE_INTENT_RE = re.compile(
    _DIRECTIVE_START
    + r"(?:delete|drop|truncate|purge|wipe|destroy|revoke|deprovision|"
    r"tear ?down|uninstall|force[- ]push)\b.{0,60}\b(?:posts?|files?|records?|"
    r"contacts?|leads?|subscribers?|accounts?|users?|database|schema|tables?|"
    r"repository|repo|branches?|containers?|services?|deployment|integration)\b",
    re.I | re.M,
)
_BULK_CRM_INTENT_RE = re.compile(
    _DIRECTIVE_START
    + r"(?:(?:bulk|mass|batch)\b.{0,80}\b(?:contacts?|leads?|subscribers?)\b"
    r"|(?:import|dedupe|email|tag|update)\b.{0,40}\b(?:all|every)\b.{0,40}"
    r"\b(?:crm\s+)?(?:contacts?|leads?|subscribers?)\b)",
    re.I | re.M,
)
_OUTBOUND_INTENT_RE = re.compile(
    _DIRECTIVE_START
    + r"(?:publish\b(?![- ]ready\b)"
    r"|(?:send|post|schedule)\s+(?:it|this|them)\b"
    r"|send\b.{0,60}\b(?:email|newsletter|campaign|announcement|message|"
    r"facebook\s+post|linkedin\s+post)\b"
    r"|publish\s+.{0,60}\b(?:facebook|linkedin|post|article|newsletter|content|copy)\b"
    r"|post\s+(?:(?:the|this|approved|final)\s+)?(?:(?:facebook|linkedin)\s+)?"
    r"(?:post|article|content)\b"
    r"|schedule\s+(?:(?:the|this|approved|final)\s+){0,3}(?:post|email|newsletter|"
    r"campaign)\b(?!\s+(?:copy|draft|caption))"
    r"|blast\b.{0,40}\b(?:email|newsletter|campaign|announcement)\b)",
    re.I | re.M,
)


_QUOTED_COPY_LABEL_RE = re.compile(
    r"\b(?:hook|copy|quote|example|sample|caption)\b.*"
    r"(?:verbatim|word[- ]for[- ]word|below|follows?|:)\s*$",
    re.I,
)


def _actionable_text(blob: str) -> str:
    """Remove quoted/example copy before looking for executable directives.

    Content briefs routinely contain imperative hook text ("Delete bad habits")
    that describes the artifact rather than an action for the worker.  Mask
    explicit quote blocks and the contiguous block following a labelled copy
    marker; preserve every other line so genuine operational instructions still
    route. A blank line ends an unquoted labelled block.
    """
    actionable: list[str] = []
    copy_block = False
    closing_quote: Optional[str] = None
    for raw_line in blob.splitlines():
        line = raw_line.strip()
        if not line:
            if not copy_block:
                actionable.append("")
            continue
        if closing_quote:
            if closing_quote in line:
                closing_quote = None
            continue
        if copy_block:
            if re.match(r"^(?:end|close)\s+(?:hook|copy|quote|example|sample|caption)\b", line, re.I):
                copy_block = False
                continue
            if re.match(r"^action\s*:", line, re.I):
                copy_block = False
            else:
                continue
        if line.startswith(">"):
            continue
        if _QUOTED_COPY_LABEL_RE.search(line):
            copy_block = True
            # Keep only text before the label. Anything after it is artifact
            # copy, including same-line quoted examples.
            label = re.search(r"\b(?:hook|copy|quote|example|sample|caption)\b", line, re.I)
            if label and label.start() > 0:
                actionable.append(line[:label.start()])
            continue
        if ((line.startswith('"') and line.endswith('"'))
                or (line.startswith("'") and line.endswith("'"))
                or (line.startswith("“") and line.endswith("”"))):
            continue
        if line.startswith('"'):
            closing_quote = '"'
            continue
        if line.startswith("'"):
            closing_quote = "'"
            continue
        if line.startswith("“"):
            closing_quote = "”"
            continue
        # Quoted text on an instruction line is content data, not another
        # instruction. Preserve the surrounding request.
        line = re.sub(r'"[^"\n]*"|“[^”\n]*”|\'[^\'\n]*\'', "", line)
        actionable.append(line)
    text = "\n".join(actionable)
    # A request may contain multiple affirmative actions on one line ("draft
    # the post and publish it" or "draft copy; then build the API"). Give each
    # executable verb its own directive boundary. Negated clauses do not match
    # the lookahead and remain inert.
    action_verbs = (
        r"delete|drop|truncate|purge|wipe|destroy|revoke|deprovision|tear ?down|"
        r"uninstall|force[- ]push|bulk|mass|batch|import|dedupe|email|tag|update|"
        r"send|publish|post|schedule|blast|patch|refactor|debug|rewrite|change|"
        r"fix|edit|modify|update|implement|deploy|migrate|install|configure|write|"
        r"add|run|build|git|push|commit|merge|open|create"
    )
    # Semicolons and "then" unambiguously introduce another clause. "And" is
    # narrower: split only `draft and <action>` or `<content artifact> and
    # <action>`. That avoids turning descriptive copy such as "Explain how teams
    # fix Git workflows and deploy infrastructure" into engineering work.
    text = re.sub(
        rf"\s*(?:;|\bthen\b)\s*(?=(?:please\s+)?(?:{action_verbs})\b)",
        "\n",
        text,
        flags=re.I,
    )
    split_lines: list[str] = []
    content_objects = r"facebook post|linkedin post|post|copy|newsletter|script|email|article|caption|content"
    for line in text.splitlines():
        line = re.sub(
            rf"\b(draft|write|prepare|revise|produce)\s+and\s+"
            rf"(?=(?:please\s+)?(?:{action_verbs})\b)",
            r"\1\n",
            line,
            flags=re.I,
        )
        line = re.sub(
            rf"\b({content_objects})\s+and\s+"
            rf"(?=(?:please\s+)?(?:{action_verbs})\b)",
            r"\1\n",
            line,
            flags=re.I,
        )
        split_lines.append(line)
    return "\n".join(split_lines)


def _explicit_operational_class(blob: str) -> Optional[str]:
    actionable = _actionable_text(blob)
    if _DESTRUCTIVE_INTENT_RE.search(actionable):
        return "destructive_ops"
    if _BULK_CRM_INTENT_RE.search(actionable):
        return "bulk_crm"
    if _OUTBOUND_INTENT_RE.search(actionable):
        return "outbound_comms"
    return None


def _has_explicit_engineering_intent(blob: str) -> bool:
    return _TECHNICAL_DIRECTIVE_RE.search(_actionable_text(blob)) is not None


def _is_redd_content_task(blob: str, assignee: Optional[str]) -> bool:
    return (
        (assignee or "").lower() in _CONTENT_ASSIGNEES
        and _CONTENT_DOMAIN_RE.search(blob) is not None
    )


def classify(
    title: str,
    body: str,
    assignee: Optional[str] = None,
) -> Optional[str]:
    """Return the risk class for a task, or None if it needs no review.

    An explicit ``[risk:none]`` marker opts out; ``[risk:engineering]`` (or any
    known class) opts in and overrides domain detection and the keyword scan.
    """
    blob = f"{title or ''}\n{body or ''}"
    forced = _FORCE_RE.search(blob)
    if forced:
        val = forced.group(1).lower()
        if val in ("none", "low", "skip"):
            return None
        if val in REVIEWER_ROUTES:
            return val
    operational_class = _explicit_operational_class(blob)
    if operational_class:
        return operational_class
    if _has_explicit_engineering_intent(blob):
        return "engineering"
    if _is_redd_content_task(blob, assignee):
        return "content"
    for name, pat in RISK_PATTERNS:
        if pat.search(blob):
            return name
    return None


def pick_reviewer(risk_class: str, author: Optional[str]) -> Optional[str]:
    """Pick the domain reviewer, avoiding self-review except for content.

    Content feedback is revision work owned by Redd, so Redd remains the
    assignee through that review pass. Risk-bearing operational classes keep
    the original independent-review invariant.
    """
    if risk_class == "content":
        route = REVIEWER_ROUTES.get(risk_class, [])
        return route[0] if route else None
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
    risk_class = classify(title or "", body or "", assignee=assignee)
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
