"""Opt-in, bounded vault-audit completion proof for agent cron jobs.

No board/network calls. A per-fire receipt proves an exact byte range in the
canonical log, not merely a successful agent turn or a fallback spool write.
"""
import argparse

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import uuid
from datetime import datetime

PARTIAL_MARKER = "[PARTIAL_AUDIT]"


def _paths(config, run_id):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid audit run id")
    target, state = Path(config["target"]), Path(config["state_dir"])
    if not target.is_absolute() or not state.is_absolute():
        raise ValueError("audit target and state_dir must be absolute")
    return target, state / run_id


def _json_write(path, data):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


def verify(config, run_id):
    target, run = _paths(config, run_id)
    with (run / "receipt.json").open("rb") as handle:
        raw_receipt = handle.read(8193)
    if len(raw_receipt) > 8192:
        raise ValueError("oversized audit receipt")
    receipt = json.loads(raw_receipt)
    if (receipt.get("run_id") != run_id or receipt.get("target") != str(target)
            or receipt.get("status") != "ok"):
        raise ValueError("missing successful canonical-write receipt")
    offset, size = receipt["offset"], receipt["size"]
    if type(offset) is not int or type(size) is not int or offset < 0 or not 0 < size <= 1024 * 1024:
        raise ValueError("invalid audit byte range")
    with target.open("rb") as handle:
        handle.seek(offset)
        data = handle.read(size)
    if len(data) != size or hashlib.sha256(data).hexdigest() != receipt["sha256"]:
        raise ValueError("canonical vault read-back mismatch")
    if receipt["business_status"] != "ok":
        raise ValueError("audit saved; automation business status is not ok")
    return {"status": "ok", "target": str(target), "size": size, "sha256": receipt["sha256"]}


def append(config, run_id, business_status, payload):
    import fcntl  # POSIX writer only; importing the scheduler stays portable.

    target, run = _paths(config, run_id)
    if not payload or len(payload) > 1024 * 1024:
        raise ValueError("audit payload must contain 1..1048576 bytes")
    # mkdir is exclusive: a timed-out/failed attempt must never be auto-retried.
    run.mkdir(parents=True, exist_ok=False)
    pending = run / "payload.md"
    with pending.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    receipt = {"run_id": run_id, "target": str(target), "status": "partial",
               "business_status": business_status, "payload": str(pending)}
    _json_write(run / "receipt.json", receipt)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0, os.SEEK_END)
        offset = handle.tell()
        if offset:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copy2(target, str(target) + ".bak-davewrite-" + stamp)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        # Read the canonical path separately; a shadow path or lost write fails.
        with target.open("rb") as readback:
            readback.seek(offset)
            if readback.read(len(payload)) != payload:
                raise OSError("canonical vault read-back mismatch")
        receipt.update(status="ok", offset=offset, size=len(payload),
                       sha256=hashlib.sha256(payload).hexdigest())
        _json_write(run / "receipt.json", receipt)
    return verify(config, run_id)


def completion_prompt(job):
    config = job.get("vault_audit")
    if config is None:
        return None, ""
    run_id = uuid.uuid4().hex
    target, _ = _paths(config, run_id)
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), "append",
                          "--target", str(target), "--state-dir", config["state_dir"],
                          "--run-id", run_id, "--status", "ok"])
    return run_id, (
        "\n\n## Required verified local vault audit (overrides older vault/status steps)\n"
        "After all business work, create a local UTF-8 summary payload and pass it on stdin to:\n"
        f"{command}\n"
        "Replace --status ok with --status partial or --status error when any business step "
        "is incomplete. This is the ONLY vault append; do not also invoke vault-safe or the "
        "legacy vault step. The helper preserves payloads locally BEFORE vault I/O, limits "
        "I/O to 10 seconds, and reads back canonical bytes. Exit 2 means partial, not success. "
        "Do not retry a failed or timed-out append. Missing proof makes the scheduler partial. "
        "End your final response with exactly CRON_STATUS: ok, CRON_STATUS: partial, or "
        "CRON_STATUS: error to match the actual run.\n"
    )


def check_completion(job, run_id, final_response):
    config = job.get("vault_audit")
    if config is None:
        return None
    try:
        _paths(config, run_id)
        command = [sys.executable, str(Path(__file__).resolve()), "verify",
                   "--target", config["target"], "--state-dir", config["state_dir"],
                   "--run-id", run_id]
        proc = subprocess.run(command, cwd="/", capture_output=True, text=True, timeout=12,
                              stdin=subprocess.DEVNULL, env={"PATH": os.defpath})
        if proc.returncode != 0:
            raise ValueError("canonical audit verification failed or receipt missing")
        proof = json.loads(proc.stdout)
        if proof.get("status") != "ok":
            raise ValueError("audit verifier did not confirm success")
        if final_response.strip().splitlines()[-1] != "CRON_STATUS: ok":
            raise ValueError("business outcome is partial, error, or undeclared")
    except Exception as exc:
        return f"{PARTIAL_MARKER} {type(exc).__name__}: {exc}"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["append", "verify"])
    parser.add_argument("--target", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--status", choices=["ok", "partial", "error"], default="partial")
    args = parser.parse_args()
    # Kernel timer also bounds stuck filesystem access; state/payload are local.
    signal.alarm(10)
    try:
        config = {"target": args.target, "state_dir": args.state_dir}
        if args.action == "append":
            result = append(config, args.run_id, args.status, sys.stdin.buffer.read(1024 * 1024 + 1))
        else:
            result = verify(config, args.run_id)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "partial", "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())