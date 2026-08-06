---
name: sdlc-review
description: "Domain-aware review pass for a routed kanban task before it can complete."
version: 1.1.0
author: Hermes Agent (Suneet fleet)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [review, verification, gate, kanban, high-risk, independent-review]
    related_skills: [requesting-code-review, github-code-review, systematic-debugging]
---

# Domain-Aware Verification Pass

You are the **reviewer**. For operational risk classes, another agent did this
work and you provide independent verification. For the `content` class, Redd
keeps ownership: this is a domain revision pass, not engineering review.

The dispatcher force-loads this skill for every task in `review`. It is
reached automatically when a routed task tries to go `done` and the verification
gate (`hermes_cli/review_gate.py`) sends it through its domain review path.

## Read this first

1. `kanban_show(<task_id>)` — the task, its original body, comments, and event history.
2. Find the `review_gate_engaged` event. Its payload tells you the
   **risk_class**, the **original_assignee**, and the routed reviewer. Reviewers
   differ from authors for operational risk classes; `content` intentionally
   routes Redd → Redd.
3. Read the author's completion summary and any attached artifacts before you
   form an opinion.

## The standard

Default to **skeptical**. Your value here is catching what the author could not
see, so look for the failure they'd be least likely to notice. Verify claims
against reality — run the thing, read the file, query the record. An assertion
in a summary is not evidence.

Check by risk class:

- **content** — Does the draft satisfy the requested format, voice, factual
  constraints, and CTA? Fix semantic or prose defects in the content path; do
  not relabel them as engineering issues or hand them to Stark merely because
  the copy mentions systems, workflows, or building. A task that actually asks
  to send, publish, bulk-update, or delete something is operational work, not a
  content draft, and keeps its operational risk class.
- **engineering** — Does the change do what the summary says? Read the actual
  diff or file, don't trust the description. Were tests run, and did they pass?
  Any migration, schema, or config edit reversible? Any secret, token, or
  credential touched or logged?
- **research** — Is each conclusion traceable to a source that exists? Spot-check
  at least one specific number or quote against its origin. Are unverified
  inferences labelled as inferences? Would a different reading of the same data
  support a different conclusion?
- **outbound_comms** — Who receives this, and is that list right? Is anything in
  it wrong, off-voice, or something Suneet would not personally sign? Named
  clients anonymized? Any commitment, price, or date that isn't confirmed?
- **destructive_ops** — What exactly gets deleted or overwritten, and is there a
  restore path? Is the blast radius the stated one? Was the target confirmed to
  be what the author believed it to be?
- **bulk_crm** — How many records does this touch? Was the count verified against
  the source rather than assumed? Is there a dedupe or rollback plan? Is a
  partial-failure state recoverable?

## Your verdict — one of exactly two moves

**Approve.** The work holds up and you have checked it, not just read it.

```
kanban_complete(<task_id>, summary="REVIEW APPROVED (<risk_class>) by <you>: <what you actually verified and how>")
```

The gate records your pass and returns ownership to the author. Say what you
verified — "checked out" is not a review.

**Reject.** Something is wrong, unverified, or unsafe.

```
kanban_block(<task_id>, reason="REVIEW REJECTED (<risk_class>): <the specific defect and what would fix it>")
```

Be concrete enough that the author can act without asking you a follow-up
question. Name the file, the number, the claim, the recipient.

## Do not

- For operational risk classes, do not rewrite the work yourself. Reject it
  with a reason; the author owns the fix. For `content`, Redd owns the revision
  pass and may correct prose before completing.
- Do not approve because it looks plausible. If you could not verify a load-bearing
  claim, that is a rejection, and say which claim.
- Do not widen scope. You are reviewing this task, not the surrounding system.
