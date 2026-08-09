"""Canary coverage for opt-in deterministic Kanban proof gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import goals
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_proof_gate import (
    ProofGateSpecError,
    parse_proof_gate,
    run_proof_gate,
)


def _marker(
    *,
    gate_type: str = "file_equals",
    path: str = "proof.txt",
    expected: str = "PASS\n",
    max_bytes: int = 1024,
) -> str:
    payload = json.dumps(
        {
            "type": gate_type,
            "path": path,
            "expected": expected,
            "max_bytes": max_bytes,
        },
        separators=(",", ":"),
    )
    return f"<!-- hermes-proof-gate\n{payload}\n-->"


@pytest.fixture
def canary(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "stark-canary")
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()

    body = f"""Expected outcome: sandbox proof file contains PASS.
Evidence/verification: the kernel reads proof.txt and matches exact content.
Hard constraints: do not access network or customer/business data.
Boundaries/no side effects: only this temporary workspace.
Stop condition: stop after two turns or any external dependency.
Turn budget: 2.

{_marker()}
"""
    with kb.connect() as conn:
        tid = kb.create_task(
            conn,
            title="Bounded proof-gate canary",
            body=body,
            assignee="stark-canary",
            workspace_kind="dir",
            workspace_path=str(workspace),
            goal_mode=True,
            goal_max_turns=2,
        )
        claimed = kb.claim_task(conn, tid, claimer="canary:1")
        assert claimed is not None
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setattr("tools.kanban_tools._goal_judge_available", lambda: False)
    return tid, workspace


def test_parser_rejects_ambiguous_or_invalid_contracts():
    marker = _marker()
    with pytest.raises(ProofGateSpecError, match="exactly one"):
        parse_proof_gate(marker + "\n" + marker)
    with pytest.raises(ProofGateSpecError, match="relative"):
        parse_proof_gate(_marker(path="/tmp/outside"))
    with pytest.raises(ProofGateSpecError, match="relative"):
        parse_proof_gate(_marker(path=" ../outside.txt "))
    for ambiguous_path in (
        "nested//proof.txt",
        "nested/./proof.txt",
        "nested/../proof.txt",
        "nested/proof.txt/",
        r"nested\\..\\proof.txt",
    ):
        with pytest.raises(ProofGateSpecError, match="components"):
            parse_proof_gate(_marker(path=ambiguous_path))
    with pytest.raises(ProofGateSpecError, match="relative"):
        parse_proof_gate(_marker(path=r"C:\\outside\\proof.txt"))
    with pytest.raises(ProofGateSpecError, match="64-character"):
        parse_proof_gate(_marker(gate_type="file_sha256", expected="not-a-digest"))


def test_unmarked_cards_preserve_caller_metadata_compatibility(canary):
    _canary_tid, _workspace = canary
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="unmarked compatibility card")
        assert kb.claim_task(conn, tid, claimer="compatibility") is not None
        assert kb.complete_task(
            conn,
            tid,
            summary="ordinary completion",
            metadata={"proof_gate_evidence": {"caller_owned": True}},
        ) is True
        run = kb.latest_run(conn, tid)
        assert run is not None
        assert run.metadata == {"proof_gate_evidence": {"caller_owned": True}}
        completed = [
            event for event in kb.list_events(conn, tid) if event.kind == "completed"
        ]
        assert len(completed) == 1
        assert "proof_gate_evidence" not in (completed[0].payload or {})

        assert kb.edit_completed_task_result(
            conn,
            tid,
            result="edited",
            metadata={"proof_gate_evidence": {"caller_owned": "edited"}},
        ) is True
        edited = kb.latest_run(conn, tid)
        assert edited is not None
        assert edited.metadata == {
            "proof_gate_evidence": {"caller_owned": "edited"}
        }

        with pytest.raises(kb.ProofGateContractLockedError, match="immutable"):
            kb.ensure_proof_gate_body_edit_allowed(
                conn,
                tid,
                current_body=None,
                proposed_body=_marker(),
            )


def test_completed_unmarked_card_without_run_cannot_gain_proof_contract(canary):
    _canary_tid, _workspace = canary
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="manual unmarked completion")
        assert kb.complete_task(
            conn,
            tid,
            summary="manual completion",
            metadata={"proof_gate_evidence": {"passed": True, "forged": True}},
        ) is True
        assert kb.latest_run(conn, tid) is not None
        with kb.write_txn(conn):
            conn.execute("DELETE FROM task_runs WHERE task_id = ?", (tid,))
            conn.execute("DELETE FROM task_events WHERE task_id = ?", (tid,))
            conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (tid,))
        assert kb.latest_run(conn, tid) is None
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "ready"
        assert task.completed_at is not None
        with pytest.raises(kb.ProofGateContractLockedError, match="immutable"):
            kb.ensure_proof_gate_body_edit_allowed(
                conn,
                tid,
                current_body=task.body,
                proposed_body=_marker(),
            )


def test_late_concurrent_marker_is_enforced_inside_write_lock(canary, monkeypatch):
    _canary_tid, _workspace = canary
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="late marker race")

    from hermes_cli import kanban_proof_gate as pg

    original_evaluate = pg.evaluate_task_proof_gate
    first_call = True

    def add_marker_after_unmarked_preflight(task):
        nonlocal first_call
        if first_call:
            first_call = False
            assert original_evaluate(task) is None
            with kb.connect() as other:
                with kb.write_txn(other):
                    other.execute(
                        "UPDATE tasks SET body = ? WHERE id = ?",
                        (_marker(), tid),
                    )
            return None
        return original_evaluate(task)

    monkeypatch.setattr(pg, "evaluate_task_proof_gate", add_marker_after_unmarked_preflight)
    with kb.connect() as conn:
        assert kb.complete_task(conn, tid, summary="race bypass") is False
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "ready"
        events = kb.list_events(conn, tid)
        assert any(event.kind == "completion_blocked_proof_gate" for event in events)


def test_authoritative_db_gate_rejects_missing_proof_and_forged_metadata(canary):
    tid, _workspace = canary
    with kb.connect() as conn:
        with pytest.raises(kb.ProofGateRejectedError):
            kb.complete_task(
                conn,
                tid,
                summary="done — trust me",
                metadata={"proof_gate_evidence": {"passed": True, "forged": True}},
            )
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "running"
        events = kb.list_events(conn, tid)
        assert any(e.kind == "completion_blocked_proof_gate" for e in events)
        assert len(kb.list_runs(conn, task_id=tid)) == 1


def test_worker_completion_passes_and_kernel_overwrites_evidence(canary):
    from tools import kanban_tools as kt

    tid, workspace = canary
    (workspace / "proof.txt").write_text("PASS\n", encoding="utf-8")
    accepted = json.loads(
        kt._handle_complete(
            {
                "summary": "sandbox read-back now passes",
                "metadata": {
                    "proof_gate_evidence": {"passed": False, "forged": True}
                },
            }
        )
    )
    assert accepted["ok"] is True

    with kb.connect() as conn:
        task = kb.get_task(conn, tid)
        run = kb.latest_run(conn, tid)
        assert task is not None and task.status == "done"
        assert run is not None and isinstance(run.metadata, dict)
        evidence = run.metadata["proof_gate_evidence"]
        assert evidence["passed"] is True
        assert evidence["type"] == "file_equals"
        assert evidence["path"] == "proof.txt"
        assert len(evidence["observed_sha256"]) == 64
        assert "forged" not in evidence
        original_evidence = dict(evidence)
        with kb.write_txn(conn):
            conn.execute(
                "DELETE FROM task_events WHERE task_id = ? AND kind = 'completed'",
                (tid,),
            )
        assert kb.edit_completed_task_result(
            conn,
            tid,
            result="edited result",
            metadata={
                "proof_gate_evidence": {"passed": False, "forged": True},
                "note": "allowed backfill",
            },
        ) is True
        edited_run = kb.latest_run(conn, tid)
        assert edited_run is not None and isinstance(edited_run.metadata, dict)
        assert edited_run.metadata["proof_gate_evidence"] == original_evidence
        assert edited_run.metadata["note"] == "allowed backfill"
        assert kb.claim_task(conn, tid, claimer="duplicate:2") is None
        assert kb.complete_task(conn, tid, summary="duplicate") is False
        assert len(kb.list_runs(conn, task_id=tid)) == 1


def test_cli_completion_path_cannot_bypass_failing_gate(canary, capsys):
    from hermes_cli.kanban import _cmd_complete

    tid, _workspace = canary
    args = argparse.Namespace(
        task_ids=[tid], summary="done", result=None, metadata=None
    )
    assert _cmd_complete(args) == 1
    assert "deterministic proof gate" in capsys.readouterr().err
    with kb.connect() as conn:
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "running"


def test_artifact_symlink_cannot_escape_workspace(canary, tmp_path):
    tid, workspace = canary
    outside = tmp_path / "outside.txt"
    outside.write_text("PASS\n", encoding="utf-8")
    (workspace / "proof.txt").symlink_to(outside)
    with kb.connect() as conn:
        with pytest.raises(kb.ProofGateRejectedError, match="symbolic links"):
            kb.complete_task(conn, tid, summary="done")
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "running"


def test_workspace_evidence_uses_the_opened_directory_descriptor(canary, monkeypatch):
    from hermes_cli import kanban_proof_gate as pg

    tid, workspace = canary
    (workspace / "proof.txt").write_text("PASS\n", encoding="utf-8")
    original_inode = workspace.stat().st_ino
    original_reader = pg._read_workspace_file

    replacement = workspace.with_name("replacement")
    replacement.mkdir()
    (replacement / "proof.txt").write_text("PASS\n", encoding="utf-8")
    replacement_inode = replacement.stat().st_ino

    def swap_then_read(path, relative_path, *, max_bytes):
        workspace.rename(workspace.with_name("parked"))
        replacement.rename(workspace)
        return original_reader(path, relative_path, max_bytes=max_bytes)

    monkeypatch.setattr(pg, "_read_workspace_file", swap_then_read)
    with kb.connect() as conn:
        task = kb.get_task(conn, tid)
        assert task is not None
        result = pg.evaluate_task_proof_gate(task)

    assert result is not None and result.passed is True
    assert result.evidence["workspace_inode"] == replacement_inode
    assert result.evidence["workspace_inode"] != original_inode


def test_ancestor_symlink_swap_cannot_redirect_workspace_read(tmp_path, monkeypatch):
    from hermes_cli import kanban_proof_gate as pg

    root = tmp_path / "ancestor-root"
    parent = root / "parent"
    workspace = parent / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "proof.txt").write_text("FAIL\n", encoding="utf-8")

    outside_parent = root / "outside"
    outside_workspace = outside_parent / "workspace"
    outside_workspace.mkdir(parents=True)
    (outside_workspace / "proof.txt").write_text("PASS\n", encoding="utf-8")

    spec = parse_proof_gate(_marker())
    assert spec is not None
    original_opener = pg._open_workspace_directory

    def swap_then_open(path, flags):
        parent.rename(root / "parked")
        parent.symlink_to(outside_parent, target_is_directory=True)
        return original_opener(path, flags)

    monkeypatch.setattr(pg, "_open_workspace_directory", swap_then_open)
    result = pg.run_proof_gate(
        SimpleNamespace(id="ancestor-swap", workspace_path=str(workspace)),
        spec,
    )

    assert result.passed is False
    assert "symbolic links" in result.detail


def test_persisted_symlink_workspace_is_rejected(tmp_path):
    outside = tmp_path / "outside-workspace"
    outside.mkdir()
    (outside / "proof.txt").write_text("PASS\n", encoding="utf-8")
    linked_workspace = tmp_path / "linked-workspace"
    linked_workspace.symlink_to(outside, target_is_directory=True)

    spec = parse_proof_gate(_marker())
    assert spec is not None
    result = run_proof_gate(
        SimpleNamespace(id="stable-symlink", workspace_path=str(linked_workspace)),
        spec,
    )

    assert result.passed is False
    assert "symbolic links" in result.detail


def test_triage_specifier_cannot_remove_a_used_proof_contract(canary):
    tid, _workspace = canary
    with kb.connect() as conn:
        assert kb.block_task(conn, tid, reason="canary block") is True
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status = 'triage' WHERE id = ?", (tid,))

        with pytest.raises(kb.ProofGateContractLockedError, match="immutable"):
            kb.specify_triage_task(
                conn,
                tid,
                body="marker removed",
                assignee="stark-canary",
            )

        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "triage"
        assert "hermes-proof-gate" in (task.body or "")


def test_budget_exhaustion_blocks_once_and_prevents_redispatch(canary, monkeypatch):
    tid, _workspace = canary
    monkeypatch.setattr(
        goals,
        "judge_goal",
        lambda *a, **kw: (
            "continue",
            "required proof is still missing",
            False,
            None,
            False,
        ),
    )

    def status():
        with kb.connect() as conn:
            task = kb.get_task(conn, tid)
            return task.status if task is not None else None

    block_calls: list[str] = []

    def block(reason: str):
        block_calls.append(reason)
        with kb.connect() as conn:
            assert kb.block_task(conn, tid, reason=reason) is True

    result = goals.run_kanban_goal_loop(
        task_id=tid,
        goal_text="produce proof",
        run_turn=lambda prompt: "still working",
        task_status_fn=status,
        block_fn=block,
        max_turns=2,
        first_response="not done",
    )
    assert result == {
        "outcome": "blocked_budget",
        "turns_used": 2,
        "reason": "turn budget exhausted",
    }
    assert len(block_calls) == 1
    assert "(2/2)" in block_calls[0]

    with kb.connect() as conn:
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "blocked"
        assert kb.claim_task(conn, tid, claimer="duplicate:2") is None
        assert kb.claim_task(conn, tid, claimer="duplicate:3") is None
        assert len(kb.list_runs(conn, task_id=tid)) == 1
