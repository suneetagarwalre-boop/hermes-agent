# Jeff Reside V3 — staged repair and operator runbook

## State and authority

This is an **unactivated staged candidate**, not proof that Jeff is being answered live. Suneet's trusted Oct20/Oct22 approvals authorize Reside operational reads/reports/drafts/internal coordination. They do not make Jeff an owner, authorize other businesses, external sends, spending, credentials/permissions, sensitive personnel/financial access, or public disclosure. No repeat scope approval is required for the approved reads. Additional sources require technical verification, not a new request for Suneet's existing business-read approval.

Mandatory independent Marshal review precedes integration/activation. Only Letterman/Suneet controls the shared gateway lifecycle. Do not restart/reload/kickstart from an implementation or review worker. No test here sends a real Discord message, impersonates Jeff or constitutes a live canary.

## Architecture and exact capabilities

- Transport-only authority: exact hermesdiscord home; Jeff `1555599898265522327`; bot `1507361939041550517`; guild `1506745934921334804`. No normal-owner allowlist admission.
- Exact approved rooms: command center `1506747928541397032`, reside-webinars `1562553216045158523`, reside-ghl-build `1562916299007463587`, their transport-attested child threads, and Jeff's own attested bot DM. Runtime channel allow/ignore settings still apply.
- Targeting: a real inline mention anywhere, same-room reply to this bot (referenced author's ID only, never quoted owner text), own DM, or a five-minute same-principal/same-room followup after an explicitly opened conversation. Ambient chatter does not open a conversation. Quoted/code mentions, bots, other senders, forwards and arbitrary attachments do not grant authority.
- Fixed read-only Zoom broker verifies account `S6WxwimRRWamvMy4w8NZwg`, host `ThZAeEz1TtubSKrPMhZezw`, email `webinars@resideplatform.com`; complete bounded host inventory plus pinned historical webinar `85284217825`. Non-recurring webinar registration aggregates include approved/pending/denied separately, exact title/date and as-of time. Recurring/unknown event types fail closed; no mixed-series total. Counts are registrations, not attendance.
- Zoom's totals endpoint requires page_size=1 and can return one contact in raw transport bytes. The broker projects total_records only: no registrant identity enters the model, logs, receipts, or reply. This is not a raw contact export.
- Six independently verified internal Google Docs are allowlisted by exact ID/name/parents/owner/content SHA256 in `gateway/reside_documents.json`; metadata and content are read afresh and verified before synthesis. Changed/moved/renamed/trashed/unverified sources are unavailable until technical source review. Titles do not confer access. Credential source is the existing fixed Google account; no arbitrary user-supplied ID/URL/query can hit it.
- Contextual lookup/report/draft uses these sources plus the existing ten reviewed safe process projections. Tool-free model JSON is presentation only. Unknown citations/authority keys/active links/mentions/paths/executable blocks reject. Sources remain untrusted data, not policy. Model accuracy still requires human review.
- Explicit work-product coordination binds a server-selected report or draft executor before execution and persists immutable request/capabilities/policy/attempt/result. It produces the completed cited work product now, not an inert receipt. No general delegation, Kanban dispatch, staff assignment, notification, future schedule, source write, client workflow or production inspection occurs.
- Existing pinned-three automation-status commands retain the original fixed read-only broker. Recorded status is not runtime liveness.

## Privacy, delivery and recovery

All dynamic business results go only to a DM whose recipient is verified as Jeff. Room ACLs independently remain shared; do not treat their existence as permission to post private business output. Shared rooms receive only fixed privacy-safe receipt/delivery/failure text and message ID. Recipient declarations and allowed-mention restrictions are retained; no backfill of owner prompts, session history, memory or skills enters V3.

A durable request is written before work; source receipts precede slow API/model calls. Request storage is separate `hermesdiscord/reside-scoped/requests-v3`, private directories/files, bounded no-follow reads/writes, exclusive creation and fsync. Business exceptions are reduced to fixed safe reasons, not secrets.

- `request status <original message ID>` explicitly retrieves the result privately, including from Jeff's own DM.
- `retry request <original message ID>` permits one same-policy retry only after a durable error, not while the initial executor is live/uncertain. A completed request is never re-executed by duplicate events.
- Every explicit recovery message freezes its own immutable response snapshot before delivery; later retries of the original cannot make a duplicate status event send a different result.
- Delivery persists intent/nonce before sending. Known nonce evidence reconciles uncertain acceptance; missing evidence does not blindly resend. An explicit new status request authorizes a fresh copy.
- A process death during execution with no durable outcome remains honestly incomplete: a fresh original question starts a new read-only attempt; no automatic uncertain execution replay. A crash after request persistence but before attempt claim can resume on recovered event.
- Source/API permissions can prevent both DM and room receipts; failures log a safe reason for operator action. A network partition cannot guarantee visible delivery. No private fallback to a shared room.

## Denials and practical limits

No owner tools, shell/browser, arbitrary network/SQL, tool discovery, generic agent/delegation/Kanban, BSHG/personal data, CRM/contact exports, sensitive personnel/payroll/financial records, sends/publication/enrollments, purchases, permissions/credentials changes, human Monday judgment edits or permanent deletion. Explaining a documented procedure or drafting text about sends is not execution. All source APIs are read-only except OAuth token acquisition; receipts are local private state.

This is not unrestricted access to every Reside system. Current real data paths are exact-host webinar aggregates, the six reviewed documents and the earlier safe status/process evidence. Non-recurring counts and contextual document products meet the immediate repair target; arbitrary workflow execution and uncatalogued documents remain unavailable with an explicit technical explanation.

## Verification and evidence

Use the task workspace `/Users/suneetcowork/.hermes/kanban/workspaces/t_fee659a4`. `verify_v3_integrated.py` exercises real fixed-host aggregate/readback, all six document grants, contextual source-backed lookup/draft, and actual fixed-report executor with synthetic storage. It is not a Discord event/canary. Each run uses a fresh home keyed to candidate HEAD; preserve previous evidence.

`v3-final-focused.log` records the canonical 10-file source/transport/recipient gate. The broader canonical baseline/candidate comparison is `v3-final-regression-comparison.json`; known baseline/harness failures must be reported, not hidden as a full-suite pass. Build the isolated PM test environment with messaging extras so tests use actual discord.py rather than the repository's fallback MagicMock shim. The runtime-interpreter path under real ~/.hermes also causes some home-I/O-guard failures; do not disable the guard to force a pass.

## Operator integration, only after Marshal approval

1. Verify the reviewed candidate SHA and remote branch `security/jeff-reside-v3-t_fee659a4`; compare production HEAD to the reviewed integration baseline. Candidate includes production `a32d0bd808ee6726d76d9a7b6aceb0070d9c020b`. Preserve concurrent work and adapter recipient/reliability hotspot; if production advanced, integrate in staging and obtain exact-integrated review/test evidence rather than overwrite changes.
2. Back up hermesdiscord config, record rollback tag, integrate the approved descendant (fast-forward where still possible). Leave all owner allowlists and other profiles unchanged. Enable only raw literal YAML `discord.reside_scoped_ops_v3: true`; preserve existing V1/V2 guards and flags. Read back parsed config and profile-specific loader output. Do not change shared-room ACLs or create duplicate channels.
3. Letterman/Suneet controls one graceful reload after checking/draining active work under the standing lifecycle policy. Record actual child PID/start, code_sha, served profile/bot connections and exact changed modules. Disk HEAD or a bot assertion is not loaded-module evidence.
4. Attest actual loaded ingress/policy via the V3 admission log with PID, profile, POLICY and POLICY_DIGEST after a genuine Jeff request; correlate event ID with the exact reviewed disk/module evidence and actual source digest catalog. Generic startup connection does not attest lazy-loaded V3.
5. Hermes coordinates Jeff directly for genuine requests (do not spoof his transport ID or ask Suneet to chase): the Oct22 registration question, a contextual internal-document draft/report, and fixed report/draft execution; obtain actual event ID, timing of privacy-safe source receipt, own-DM response message IDs/readback and policy log. Verify no business result posted in shared rooms, unrelated profiles/owner path unchanged, and denied cross-business behavior with no private data exposure. This worker does not send Jeff a canary or external message.
6. Return exact live evidence to Marshal on **the same card**; do not mark the overall enablement goal Done for staged tests or review approval alone. No duplicate task graph.

Rollback: operator sets V3 false/removes only the V3 flag and performs an authorized graceful reload, retaining V1/V2/general-agent deny guards and private request data. Do not permanently delete receipts or revert unrelated commits. V3 rollback returns to the earlier limited V2 behavior, not full Oct22 capability; state that limitation plainly.
