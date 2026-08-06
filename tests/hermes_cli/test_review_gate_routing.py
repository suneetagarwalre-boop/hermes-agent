"""Regression coverage for domain-first verification-gate routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import review_gate


PERRY_CONTENT_TITLE = "Rewrite Reside Facebook lead-follow-up post in Perry voice"
PERRY_CONTENT_BODY = """Suneet approved this direction.

Write the complete Facebook post in the Perry voice.
Introduce Reside naturally as the kind of operating system that creates clear
ownership, a standard workflow, and visible accountability. Build the post
around a real-estate team leader's lived experience.
Return only the complete post. No publishing, Notion, Monday, or Drive writes;
in-chat review only.
"""

ENGINEERING_TITLE = "Fix the review classifier and reviewer-routing logic"
ENGINEERING_BODY = """Patch the Python code in hermes_cli/review_gate.py, add
pytest regression tests, commit the git changes, and deploy the updated Hermes
control-plane routing configuration.
"""


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Use an isolated board so routing tests are always safe dry-runs."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _gate_event(conn, task_id):
    return next(
        event
        for event in kb.list_events(conn, task_id)
        if event.kind == review_gate.GATE_EVENT
    )


def test_redd_content_domain_wins_over_incidental_engineering_words():
    risk_class = review_gate.classify(
        PERRY_CONTENT_TITLE,
        PERRY_CONTENT_BODY,
        assignee="redd",
    )

    assert risk_class == "content"
    assert review_gate.pick_reviewer(risk_class, "redd") == "redd"


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Draft a Facebook post", "Write sharp copy about a better operating system."),
        ("Write a LinkedIn post", "Explain how teams build software and use Git workflows."),
        ("Draft a nurture email", "Create the email copy; no sending or publishing."),
        ("Write a listing video script", "Use Perry voice and return only the script."),
        ("Revise voice work", "Tighten the spoken copy without changing its meaning."),
        ("Create Notion content", "Draft the article for later storage in Drive."),
        (
            "Draft a LinkedIn post",
            "Write content explaining how to rewrite Python code safely.",
        ),
        (
            "Draft a technical Facebook post",
            "Explain why teams need a technical implementation before scaling.",
        ),
        (
            "Draft a newsletter",
            "Explain Git commits and Docker infrastructure in plain English.",
        ),
        (
            "Draft a LinkedIn post",
            "Explain how teams fix Git workflows and deploy infrastructure safely.",
        ),
        (
            "Build a Facebook post about API infrastructure",
            "Return only the draft.",
        ),
        (
            "Rewrite this LinkedIn post about Python code",
            "Return only the copy.",
        ),
        (
            "Draft a Facebook post",
            "Use this hook verbatim:\nDelete bad habits before they delete momentum.",
        ),
        (
            "Draft a Facebook post",
            "Use this hook verbatim:\nDelete old Facebook posts before they delete momentum.",
        ),
        (
            "Draft a LinkedIn post",
            'Quoted copy: "Publish bold ideas. Build systems. Rewrite the rules. '
            'Delete bad habits."',
        ),
        (
            "Draft a Facebook post",
            "Hook below:\nDelete old Facebook posts.\nPublish approved newsletter.\n"
            "Build an API service.\nRewrite Python code.",
        ),
        (
            "Draft a Facebook post",
            '"Delete old Facebook posts.\nPublish approved newsletter.\n'
            'Build an API service.\nRewrite Python code."',
        ),
        (
            "Draft a Facebook post",
            "Hook follows\nDelete old Facebook posts.\nPublish approved newsletter.",
        ),
        (
            "Draft a Facebook post",
            "Hook below:\nDelete old Facebook posts.\n\nPublish approved newsletter.\n"
            "Build an API service.",
        ),
        (
            "Publish-ready Facebook post",
            "Draft the final copy; do not publish it.",
        ),
        (
            "Action: draft final Facebook post; do not publish",
            "Return only the final copy.",
        ),
        (
            "Schedule post copy",
            "Draft a caption about scheduling; no publishing.",
        ),
        (
            "Draft an email to the CRM admin",
            "Email the CRM admin about the new post copy.",
        ),
    ],
)
def test_redd_content_formats_keep_content_review_path(title, body):
    risk_class = review_gate.classify(title, body, assignee="redd")

    assert risk_class == "content"
    assert review_gate.pick_reviewer(risk_class, "redd") == "redd"


def test_explicit_engineering_intent_still_routes_to_stark():
    risk_class = review_gate.classify(
        ENGINEERING_TITLE,
        ENGINEERING_BODY,
        assignee="dave",
    )

    assert risk_class == "engineering"
    assert review_gate.pick_reviewer(risk_class, "dave") == "stark"


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Patch the Python source code", "Fix the routing bug and add pytest tests."),
        ("Build an API service", "Implement the integration and commit the Git changes."),
        ("Deploy Hermes infrastructure", "Configure the launchd plist and Docker container."),
        ("Rewrite Python code", "Return the corrected module and regression tests."),
        ("Build infrastructure", "Build a Docker container for the service."),
        ("Technical implementation", "Change the Git workflow and commit the changes."),
        ("Technical task", "Please build an API service."),
        ("Technical task", "Could you build a new CLI package?"),
        ("Draft a Facebook post and fix the Git workflow", "Return the draft too."),
        ("Draft a Facebook post and create a Git branch", "Commit no content."),
        ("Draft a Facebook post; then push the Git changes", "Run the task."),
        ("Draft a Facebook post; update the server config", "Run the task."),
        ("Draft a Facebook post and edit the Python module", "Run the task."),
        ("Draft a Facebook post; then build an API service", "Run the task."),
    ],
)
def test_explicit_technical_work_assigned_to_redd_can_still_route_to_stark(
    title,
    body,
):
    risk_class = review_gate.classify(title, body, assignee="redd")

    assert risk_class == "engineering"
    assert review_gate.pick_reviewer(risk_class, "redd") == "stark"


@pytest.mark.parametrize(
    ("title", "body", "expected_class", "expected_reviewer"),
    [
        (
            "Action: publish approved newsletter",
            "",
            "outbound_comms",
            "katt",
        ),
        (
            "Action: draft and publish the approved Facebook post",
            "",
            "outbound_comms",
            "katt",
        ),
        (
            "Action: draft final Facebook post; then publish it",
            "",
            "outbound_comms",
            "katt",
        ),
        (
            "Schedule the approved post",
            "",
            "outbound_comms",
            "katt",
        ),
        (
            "Draft a Facebook post",
            "Copy below:\nDream bigger.\n\nAction: publish approved newsletter",
            "outbound_comms",
            "katt",
        ),
        (
            "Delete old Facebook posts",
            "Please delete all Facebook posts from the company page.",
            "destructive_ops",
            "stark",
        ),
        (
            "Email all CRM contacts",
            "Please email every CRM contact in one batch.",
            "bulk_crm",
            "martin",
        ),
        (
            "Send Facebook post",
            "Go ahead and publish the approved Facebook post to clients.",
            "outbound_comms",
            "katt",
        ),
    ],
)
def test_explicit_operational_actions_are_not_mistaken_for_content(
    title,
    body,
    expected_class,
    expected_reviewer,
):
    risk_class = review_gate.classify(title, body, assignee="redd")

    assert risk_class == expected_class
    assert risk_class is not None
    assert review_gate.pick_reviewer(risk_class, "redd") == expected_reviewer


def test_single_record_crm_update_is_not_bulk_crm():
    assert (
        review_gate.classify(
            "Update one contact",
            "Update the CRM contact phone number.",
            assignee="redd",
        )
        != "bulk_crm"
    )


def test_content_completion_routes_redd_to_redd_and_preserves_source_data(
    kanban_home,
):
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title=PERRY_CONTENT_TITLE,
            body=PERRY_CONTENT_BODY,
            assignee="redd",
            created_by="user",
        )
        comment_id = kb.add_comment(
            conn,
            task_id,
            author="suneet",
            body="Keep the approved hook as the first line.",
        )
        assert kb.claim_task(conn, task_id, claimer="dry-run:redd") is not None

        assert kb.complete_task(
            conn,
            task_id,
            summary="Complete Facebook draft produced for in-chat review only.",
            metadata={"output_target": "in-chat review only"},
        )

        routed = kb.get_task(conn, task_id)
        event = _gate_event(conn, task_id)
        comments = kb.list_comments(conn, task_id)

        assert routed.status == "review"
        assert routed.assignee == "redd"
        assert routed.body == PERRY_CONTENT_BODY
        assert [(c.id, c.author, c.body) for c in comments] == [
            (comment_id, "suneet", "Keep the approved hook as the first line.")
        ]
        assert event.payload == {
            "risk_class": "content",
            "original_assignee": "redd",
            "reviewer": "redd",
            "author_summary": "Complete Facebook draft produced for in-chat review only.",
        }

        claimed = kb.claim_review_task(
            conn,
            task_id,
            claimer="dry-run:redd-content-review",
        )
        assert claimed is not None
        assert claimed.assignee == "redd"
        review_run = kb.latest_run(conn, task_id)
        assert review_run is not None
        assert review_run.profile == "redd"

        feedback = "Semantic issue: add the missing word 'nothing'."
        assert kb.block_task(
            conn,
            task_id,
            reason=feedback,
            kind="needs_input",
            expected_run_id=review_run.id,
        )
        blocked_event = next(
            event
            for event in reversed(kb.list_events(conn, task_id))
            if event.kind == "blocked"
        )
        feedback_run = next(
            run for run in kb.list_runs(conn, task_id) if run.id == blocked_event.run_id
        )
        assert feedback_run.profile == "redd"
        assert feedback_run.summary == feedback
        assert kb.get_task(conn, task_id).assignee == "redd"


def test_engineering_completion_routes_to_stark_in_isolated_dry_run(kanban_home):
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title=ENGINEERING_TITLE,
            body=ENGINEERING_BODY,
            assignee="dave",
            created_by="user",
        )
        assert kb.claim_task(conn, task_id, claimer="dry-run:dave") is not None

        assert kb.complete_task(
            conn,
            task_id,
            summary="Classifier code and regression tests implemented.",
        )

        routed = kb.get_task(conn, task_id)
        event = _gate_event(conn, task_id)
        assert routed.status == "review"
        assert routed.assignee == "stark"
        assert event.payload["risk_class"] == "engineering"
        assert event.payload["original_assignee"] == "dave"
        assert event.payload["reviewer"] == "stark"
