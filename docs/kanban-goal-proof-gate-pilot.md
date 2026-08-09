# Bounded Kanban goal-mode proof-gate canary

Status: opt-in pilot only. Cards without the `hermes-proof-gate` marker behave exactly as before. This does not enable goal mode globally.

## Exact reusable card template

Create only one future Stark reliability/engineering card from this template and set `goal_mode=true`, `goal_max_turns=4`.

```text
Title: <one narrow reliability/engineering outcome>
Assignee: stark
Goal mode: true
Goal max turns: 4
Workspace: scratch, dir, or worktree as required

Expected outcome:
<One observable end state.>

Evidence/verification:
<The exact local artifact/read-back that proves the outcome. The proof-gate below must match it exactly.>

Hard constraints:
- No customer/business data.
- No secrets or PII in the proof artifact.
- No destructive operations.
- No deployment, notification, CRM, calendar, or Monday writes.

Boundaries/no side effects:
<Exact workspace paths and local-only systems allowed. Everything else is out of scope.>

Stop condition:
Stop and report blocked/partial if the gate cannot pass within four turns, an external dependency is required, or any boundary would need to be crossed.

Turn budget: 4.

<!-- hermes-proof-gate
{"type":"file_equals","path":"proof.txt","expected":"PASS\n","max_bytes":1024}
-->
```

The marker is authoritative JSON. Exactly one marker is allowed.

Supported bounded read-back types:

- `file_equals`: SHA-256 of the file bytes must equal SHA-256 of UTF-8 `expected`.
- `file_sha256`: SHA-256 of the file bytes must equal the 64-character hex digest in `expected`.

`path` must be relative to, resolve inside, and name a regular file in the persisted task workspace. Symlinks escaping the workspace fail closed. `max_bytes` must be 1–1,048,576.

Card text never becomes a shell command. This avoids creating a new command-execution path outside Hermes terminal approvals. The worker may run its normal test/build/read-back through existing approved tools, but the completion gate itself only verifies the resulting bounded local artifact.

## Deterministic completion behavior

Every completion surface reaches the same authoritative DB transition gate:

1. Read the proof-gate marker from the persisted card body.
2. Resolve the declared proof artifact inside the persisted task workspace.
3. Reject missing, escaping, whitespace-padded/traversing, symlinked, non-regular, oversized, unreadable, or mismatched evidence while the card remains in-flight.
4. Record an auditable `completion_blocked_proof_gate` event for a rejected attempt.
5. On a match, overwrite any caller-supplied `proof_gate_evidence` with kernel-generated metadata: gate type, check time, relative path, byte count, observed SHA-256, and expected SHA-256. Raw content is not persisted, and only gated completions promote evidence into the completed-event audit payload.
6. Re-read the persisted card under the SQLite write lock before transitioning to `done`, so a marker added after an unmarked preflight is still enforced.
7. Open every absolute workspace component descriptor-relative without following symlinks, then record workspace and file identity from the same descriptors used for the read-back.
8. Lock adding, removing, or changing a proof-gated body after its first run or completion attempt, including after requeue or run/event cleanup.
9. The existing goal judge remains an advisory pre-check; it cannot bypass the deterministic database gate or supply its own gate evidence.
10. Preserve canonical proof evidence from the completed run during result/metadata backfills, even after completed-event garbage collection.
11. The existing bounded goal loop blocks the card once its turn budget is exhausted.

Because enforcement lives in `kanban_db.complete_task`, worker `kanban_complete`, `hermes kanban complete`, dashboard completion, and internal direct callers cannot bypass it.

## Sandbox validation result

Command:

```bash
PYTHONPATH="$PWD" /Users/suneetcowork/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/hermes_cli/test_kanban_proof_gate.py \
  tests/hermes_cli/test_kanban_goal_mode.py \
  tests/hermes_cli/test_kanban_cli.py \
  tests/hermes_cli/test_kanban_db.py \
  tests/hermes_cli/test_kanban_specify.py \
  tests/hermes_cli/test_kanban_specify_db.py \
  tests/tools/test_kanban_tools.py \
  tests/plugins/test_kanban_dashboard_plugin.py \
  -o 'addopts=' -q
```

The result was `104 passed, 2 warnings in 6.60s`; both warnings were pre-existing third-party deprecations (`httpx`/Starlette TestClient and Python `audioop`).

The canary uses a temporary Hermes home, temporary SQLite board, temporary workspace, and local `proof.txt`; it makes no network or business-system calls.

Required proven cases:

- A direct DB completion with missing proof and forged metadata is rejected; the task stays `running` and an audit event is recorded.
- Writing the exact expected read-back (`PASS\n`) allows completion; forged evidence is overwritten by kernel-generated hashes.
- The CLI completion path cannot bypass a failing gate.
- A symlink to matching evidence outside the workspace is rejected.
- Whitespace-padded, empty, dot, dot-dot, POSIX-absolute, and Windows-absolute components are rejected before normalization; ancestor/final symlink swaps are also rejected, and workspace/file identity comes from the descriptors actually read.
- Dashboard and triage-specifier edits cannot add, remove, or weaken a proof contract after its first run, including after requeue; a combined body-plus-done dashboard request is rejected.
- Result/metadata backfills cannot overwrite canonical proof evidence after completed-event cleanup, while unmarked cards retain ordinary caller-owned metadata compatibility.
- A two-turn budget exhaustion produces `blocked_budget`, writes a clear `(2/2)` reason, and transitions the task to `blocked`.
- Two subsequent claim attempts against the blocked card return no claim; a completed canary also cannot be claimed again. Each path retains one run, proving no duplicate re-dispatch in the validation.

## Recommended future task classes

Use this pilot only for narrow, deterministic Stark work:

- A generated local config/report whose exact bytes or expected digest are known.
- A test/build step that emits a bounded machine-readable receipt inside the workspace.
- A local config read-back or health snapshot with an exact expected fixture.
- A sandbox migration/fixture check against disposable data.

Do not use it for:

- Customer/business records, CRM, calendar, Monday, outbound messages, or other SaaS writes.
- Deployments or infrastructure mutations whose rollback is not local and immediate.
- Subjective research, writing quality, routing judgment, or human approval.
- Long/flaky network work, open-ended debugging, or interactive checks.
- Any evidence file containing credentials, tokens, PII, or customer data.

## Rollback

Per-card rollback is immediate: create the next card with `goal_mode=false` and omit the `hermes-proof-gate` marker. No service restart, credential change, DB migration, or global config change is involved.

Code rollback: revert the pilot commit that adds `hermes_cli/kanban_proof_gate.py` and its completion-path integrations. Existing cards without the marker are behaviorally unchanged before and after rollback.

Pilot stop rule: do not broaden rollout if the first future Stark card has a false pass, false block, duplicate run, missing metadata evidence, or requires manual rescue because the contract was underspecified.
