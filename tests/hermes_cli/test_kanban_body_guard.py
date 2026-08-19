"""Unit tests for fail-closed Kanban work-brief validation."""

import io

import pytest

from hermes_cli.kanban_body_guard import (
    BriefValidationError,
    is_no_card_status_query,
    is_placeholder_body,
    resolve_body,
    validate_body,
)


VALID_CONTENT_BRIEF = """\
Action: Draft the launch email from the approved messaging.
Source: Google Doc 1AbC-launch-copy, section "Final positioning".
Scope: Include subject, preview, and body; exclude SMS and landing-page copy.
Acceptance: Return one complete draft grounded only in the named section.
If absent: Stop and report that the Google Doc or section could not be found.
"""

VALID_INFRA_BRIEF = """\
## Requested action
Update the Hermes gateway health probe.
## Source / system
Repository NousResearch/hermes-agent, gateway/status.py.
## Scope / inclusions / exclusions
Include probe and regression tests; exclude routing and alert redesign.
## Acceptance check
Run the focused pytest file and show all tests passing.
## If source absent
Stop and report the missing repository path; do not invent a replacement.
"""


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "   ",
        "\n",
        "\t\n  ",
        "-",
        " - ",
        "--",
        "---",
        ".",
        "..",
        "...",
        "?",
        "n/a",
        "N/A",
        "None",
        "null",
        "tbd",
        "TODO",
        "(empty)",
        "no body",
        "placeholder",
        "***",
        "___",
    ],
)
def test_blank_and_placeholder_bodies_are_rejected(body):
    assert is_placeholder_body(body) is True
    with pytest.raises(BriefValidationError):
        validate_body(body)


@pytest.mark.parametrize("body", [VALID_CONTENT_BRIEF, VALID_INFRA_BRIEF])
def test_valid_structured_specialist_briefs_pass(body):
    assert validate_body(body, require_structured=True) == body


def test_incomplete_brief_reports_exact_missing_fields():
    body = """\
Action: Patch the worker.
Source: GitHub issue #123.
Acceptance: Run the focused test.
"""
    with pytest.raises(BriefValidationError) as exc_info:
        validate_body(body, require_structured=True)

    error = str(exc_info.value)
    assert "scope, including inclusions and exclusions" in error
    assert "what to do if the source is absent" in error
    assert "requested action" not in exc_info.value.missing
    assert "acceptance check" not in exc_info.value.missing


def test_unassigned_draft_still_needs_real_body_but_not_structured_labels():
    body = "Investigate whether this belongs in the specialist queue."
    assert validate_body(body, require_structured=False) == body
    with pytest.raises(BriefValidationError):
        validate_body(None, require_structured=False)


def test_allow_missing_cannot_bypass_fail_closed_creation():
    with pytest.raises(BriefValidationError):
        validate_body(None, allow_missing=True)


def test_dash_reads_multiline_stdin_instead_of_being_stored():
    assert resolve_body("-", stdin=io.StringIO(VALID_INFRA_BRIEF)) == VALID_INFRA_BRIEF


def test_dash_with_empty_stdin_fails_loudly():
    with pytest.raises(BriefValidationError, match="contained no work brief"):
        resolve_body("-", stdin=io.StringIO(""))


def test_dash_on_tty_fails_loudly():
    class Tty(io.StringIO):
        def isatty(self):
            return True

    with pytest.raises(BriefValidationError, match="stdin is a terminal"):
        resolve_body("-", stdin=Tty(""))


def test_simple_status_query_is_explicitly_no_card_work():
    title = "What's the status of task t_44ad6ec3?"
    assert is_no_card_status_query(title, None) is True
    with pytest.raises(BriefValidationError, match="Answer it directly"):
        validate_body(None, title=title)


def test_status_prefix_with_action_brief_is_not_misclassified():
    title = "What's the status of task t_44ad6ec3?"
    assert is_no_card_status_query(title, VALID_INFRA_BRIEF) is False
    assert validate_body(
        VALID_INFRA_BRIEF,
        require_structured=True,
        title=title,
    ) == VALID_INFRA_BRIEF
