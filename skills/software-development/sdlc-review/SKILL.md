---
name: sdlc-review
description: "Independent verification pass for a high-risk kanban task before it can complete."
version: 1.0.0
author: Hermes Agent (Suneet fleet)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [review, verification, gate, kanban, high-risk, independent-review]
    related_skills: [requesting-code-review, github-code-review, systematic-debugging]
---

# Independent Verification Pass

You are the **reviewer**, not the author. Another agent did this work and moved
it into the review lane. Your job is to decide whether it is actually correct
and safe to complete — not to redo it, and not to rubber-stamp it.

The dispatcher force-loads this skill for every task in `review`. It is
reached automatically: a high-risk task that tried to go `done` was routed here
instead by the verification gate (`hermes_cli/review_gate.py`).

## Read this first

1. `kanban_show(<task_id>)` — the task, its body, and its event history.
2. Find the `review_gate_engaged` event. Its payload tells you the
   **risk_class**, the **original_assignee** (the author), and that you were
   picked precisely because you are not them.
3. Read the author's completion summary and any attached artifacts before you
   form an opinion.

## The standard

Default to **skeptical**. Your value here is catching what the author could not
see, so look for the failure they'd be least likely to notice. Verify claims
against reality — run the thing, read the file, query the record. An assertion
in a summary is not evidence.

Check by risk class:

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

- Do not rewrite the work yourself. Reject it with a reason; the author owns the fix.
- Do not approve because it looks plausible. If you could not verify a load-bearing
  claim, that is a rejection, and say which claim.
- Do not widen scope. You are reviewing this task, not the surrounding system.
