"""Local fixtures only: no cron agent, board, network, or real vault accesses."""
import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from cron import audit_completion as audit


@pytest.fixture
def case(tmp_path):
    config = {"target": str(tmp_path / "vault" / "audit.md"),
              "state_dir": str(tmp_path / "state")}
    return config, uuid.uuid4().hex


def test_append_canonical_readback_and_backup(case):
    config, run_id = case
    target = Path(config["target"])
    target.parent.mkdir()
    target.write_bytes(b"prior\n")
    result = audit.append(config, run_id, "ok", b"new audit\n")
    assert result["status"] == "ok"
    assert target.read_bytes() == b"prior\nnew audit\n"
    backups = list(target.parent.glob("audit.md.bak-davewrite-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"prior\n"
    assert audit.check_completion({"vault_audit": config}, run_id, "Report\nCRON_STATUS: ok") is None


def test_missing_receipt_is_partial_even_if_agent_claims_ok(case):
    config, run_id = case
    assert audit.PARTIAL_MARKER in (audit.check_completion({"vault_audit": config}, run_id, "CRON_STATUS: ok") or "")


def test_canonical_tampering_is_partial(case):
    config, run_id = case
    audit.append(config, run_id, "ok", b"audit\n")
    Path(config["target"]).write_bytes(b"wrong\n")
    assert audit.check_completion({"vault_audit": config}, run_id, "CRON_STATUS: ok")


@pytest.mark.parametrize("status", ["partial", "error"])
def test_business_partial_does_not_become_ok(case, status):
    config, run_id = case
    with pytest.raises(ValueError, match="business status"):
        audit.append(config, run_id, status, b"partial audit\n")
    assert Path(config["target"]).read_bytes() == b"partial audit\n"
    assert audit.check_completion({"vault_audit": config}, run_id, "CRON_STATUS: ok")


@pytest.mark.parametrize("response", ["CRON_STATUS: partial", "CRON_STATUS: error", "all done", ""])
def test_business_declaration_required(case, response):
    config, run_id = case
    audit.append(config, run_id, "ok", b"audit\n")
    assert audit.check_completion({"vault_audit": config}, run_id, response)


def test_stale_receipt_does_not_satisfy_new_fire(case):
    config, run_id = case
    audit.append(config, run_id, "ok", b"audit\n")
    assert audit.check_completion({"vault_audit": config}, uuid.uuid4().hex, "CRON_STATUS: ok")


def test_failed_write_preserves_payload_and_returns_partial(case):
    config, run_id = case
    target = Path(config["target"])
    target.parent.mkdir()
    target.mkdir()  # deterministic IO failure, including when running as root
    with pytest.raises(IsADirectoryError):
        audit.append(config, run_id, "ok", b"must survive\n")
    run = Path(config["state_dir"]) / run_id
    assert (run / "payload.md").read_bytes() == b"must survive\n"
    assert json.loads((run / "receipt.json").read_text())["status"] == "partial"
    assert audit.check_completion({"vault_audit": config}, run_id, "CRON_STATUS: ok")


def test_no_retry_or_duplicate_append(case):
    config, run_id = case
    audit.append(config, run_id, "ok", b"once\n")
    with pytest.raises(FileExistsError):
        audit.append(config, run_id, "ok", b"twice\n")
    assert Path(config["target"]).read_bytes() == b"once\n"


def test_no_optin_is_unchanged():
    assert audit.completion_prompt({}) == (None, "")
    with patch.object(audit.subprocess, "run", side_effect=AssertionError("must not execute")):
        assert audit.check_completion({}, None, "partial prose") is None


def test_fresh_prompt(case):
    config, _ = case
    first, text = audit.completion_prompt({"vault_audit": config})
    second, _ = audit.completion_prompt({"vault_audit": config})
    assert first is not None and first != second and first in text and "CRON_STATUS" in text
    assert str(Path(audit.__file__).resolve()) in text


@pytest.mark.parametrize("failure", [FileNotFoundError("helper unavailable"), subprocess.TimeoutExpired("verify", 12)])
def test_helper_execution_failure_propagates(case, failure):
    config, run_id = case
    with patch.object(audit.subprocess, "run", side_effect=failure):
        assert audit.check_completion({"vault_audit": config}, run_id, "CRON_STATUS: ok")


@pytest.mark.parametrize("bad", ["../bad", "", "x" * 32])
def test_invalid_run_id_rejected(case, bad):
    config, _ = case
    with pytest.raises(ValueError):
        audit.append(config, bad, "ok", b"bad")


def test_scheduler_partial_persistence_and_delivery(monkeypatch, tmp_path):
    # Exercise the real completion state transition, never the cron agent.
    from cron import scheduler as scheduler
    from cron import jobs
    monkeypatch.setattr(jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    job = jobs.create_job(prompt="fixture only", schedule="every 1h", name="audit fixture")
    job["vault_audit"] = {"target": str(tmp_path / "vault.md"), "state_dir": str(tmp_path / "state")}
    error = audit.PARTIAL_MARKER + " TimeoutExpired: local audit exceeded bound"
    final = "PARTIAL: local audit unavailable; payload preserved"
    monkeypatch.setattr(scheduler, "run_job", lambda *a, **kw: (False, "fixture", final, error))
    monkeypatch.setattr(scheduler, "save_job_output", lambda *a: str(tmp_path / "output.md"))
    delivered = []
    monkeypatch.setattr(scheduler, "_deliver_result", lambda j, content, **kw: delivered.append(content))
    monkeypatch.setattr(scheduler, "create_execution", lambda *a, **kw: {"id": "fixture"})
    monkeypatch.setattr(scheduler, "mark_execution_running", lambda *a: {})
    monkeypatch.setattr(scheduler, "finish_execution", lambda *a, **kw: None)
    monkeypatch.setattr(scheduler, "claim_dispatch", lambda *a: True)
    assert scheduler.run_one_job(job) is True
    persisted = jobs.get_job(job["id"])
    assert persisted["last_status"] == "partial"
    assert persisted["last_error"] == error
    assert delivered == [final]
    assert "provider" not in delivered[0]


def test_cli_failure_has_nonzero_exit(case):
    config, run_id = case
    result = subprocess.run([sys.executable, audit.__file__, "verify", "--target", config["target"],
                             "--state-dir", config["state_dir"], "--run-id", run_id],
                            capture_output=True, text=True, timeout=12)
    assert result.returncode == 2 and json.loads(result.stdout)["status"] == "partial"