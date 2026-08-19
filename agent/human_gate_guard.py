"""Hard stop for repeated human-only login, auth, and permission walls.

The guard is deliberately deterministic. It observes tool results once per
agent iteration, counts repeated human gates, and emits exactly three actionable
lines at the cap instead of spending the rest of the run retrying a wall only a
human can clear.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Optional

HUMAN_GATE_ITERATION_CAP = 10

_SUCCESS_RE = re.compile(
    r'"(?:success|authorized|authenticated|permission_granted)"\s*:\s*true'
    r"|\b(?:authorization|authentication|permission) (?:complete|granted|succeeded)\b",
    re.IGNORECASE,
)
_GATE_PATTERNS = (
    ("mfa", re.compile(r"\b(?:mfa|two[- ]factor|2fa|verification code)\b.*\b(?:required|needed|challenge|complete)\b", re.IGNORECASE | re.DOTALL)),
    ("oauth", re.compile(r"\b(?:oauth|authorization)\b.*\b(?:required|authorize|approve|consent|human)\b", re.IGNORECASE | re.DOTALL)),
    ("login", re.compile(r"\b(?:login wall|sign[ -]?in required|please log in|authentication required)\b", re.IGNORECASE)),
    ("reboot", re.compile(r"\b(?:reboot|restart) required\b|\brequires (?:a )?(?:reboot|restart)\b", re.IGNORECASE)),
    ("permission", re.compile(r"\bpermission required\b|\bgrant .*\b(?:permission|access)\b|\baccessibility access\b", re.IGNORECASE)),
)

_ACTIONS = {
    "login": "Sign in to the required account.",
    "oauth": "Authorize the requested OAuth connection.",
    "mfa": "Complete the MFA challenge.",
    "permission": "Grant the requested permission.",
    "reboot": "Restart the affected system.",
}
_COMPLETION = {
    "login": "sign-in is complete",
    "oauth": "authorization is complete",
    "mfa": "MFA is complete",
    "permission": "permission is granted",
    "reboot": "the restart is complete",
}
_DEFAULT_PATHS = {
    "login": "Hermes terminal → run `hermes login` and complete the displayed sign-in flow.",
    "oauth": "Hermes terminal → run `hermes login` and complete the displayed authorization flow.",
    "mfa": "Open the MFA prompt in the account sign-in window shown by the blocking tool.",
    "permission": "macOS System Settings → Privacy & Security → Accessibility.",
    "reboot": "Apple menu → Restart…",
}


def _text(result: Any) -> str:
    if isinstance(result, Mapping):
        return str(result.get("content") or result.get("result") or "")
    return str(result or "")


def _gate_kind(text: str) -> Optional[str]:
    for kind, pattern in _GATE_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def _direct_path(kind: str, text: str) -> str:
    """Return a trusted interface path, never a URL copied from tool content.

    Tool results can contain arbitrary webpage/document text. Echoing the first
    discovered URL as an imperative path would turn prompt injection into a
    phishing instruction. Fixed interface paths satisfy the human-gate contract
    without crossing that trust boundary.
    """
    if kind == "permission":
        lower = text.casefold()
        for pane in (
            "Accessibility",
            "Screen Recording",
            "Full Disk Access",
            "Automation",
            "Files and Folders",
            "Camera",
            "Microphone",
        ):
            if pane.casefold() in lower:
                return f"macOS System Settings → Privacy & Security → {pane}."
    return _DEFAULT_PATHS[kind]


@dataclass
class HumanGateGuard:
    cap: int = HUMAN_GATE_ITERATION_CAP
    iterations: int = 0
    _kind: Optional[str] = None

    def observe(
        self,
        tool_results: Iterable[Any],
        *,
        blocked_work: str = "this task",
    ) -> Optional[str]:
        """Observe one iteration; return the three-line hard-stop at the cap."""
        texts = [_text(result) for result in tool_results]
        combined = "\n".join(text for text in texts if text)

        kind = _gate_kind(combined)
        # A success result only clears the streak when this iteration no longer
        # contains the same human gate. Mixed batches often include unrelated
        # successful reads next to one still-blocked auth call.
        if kind is None and _SUCCESS_RE.search(combined):
            self.iterations = 0
            self._kind = None
            return None

        if kind is None:
            return None
        if kind != self._kind:
            self._kind = kind
            self.iterations = 0
        self.iterations += 1
        if self.iterations < self.cap:
            return None

        work = " ".join(str(blocked_work or "this task").split()).strip(" .")
        return "\n".join(
            (
                f"Action: {_ACTIONS[kind]}",
                f"Path: {_direct_path(kind, combined)}",
                f"Blocked: {work} cannot continue until {_COMPLETION[kind]}.",
            )
        )
