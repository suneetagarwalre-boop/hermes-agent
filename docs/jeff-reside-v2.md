# Jeff Reside scoped operations V2 — staged operator/reviewer guide

Task: t_fee659a4. Scope: hermesdiscord only. V2 is not enabled by this implementation.

## Four-category capability acceptance

- Lookups: ordinary natural-language questions about ten reviewed Reside operating-rule projections: business context/training, extraction scope/routing/source QA/handoff, meeting classification/matching QA, cadence evidence/tiers. Answers cite reviewed records, not raw documents. Fixed read-only recorded status for three previously verified automation IDs remains available; recorded status does not prove service liveness.
- Reports: contextual comparative analysis with source citations, documented facts separated from recommendations and missing live evidence. No actual client/personnel/financial findings are available.
- Drafts: contextual internal SOPs, checklists, briefs and working drafts synthesized from those sources and requester-supplied assumptions. Not fixed templates; marked unapproved and unsent.
- Coordination: explicit top-level task request can dispatch only the fixed internal report/draft executor. A second fresh stateless synthesis actually produces the work product using fresh safe evidence and a fixed output kind. Immutable private request/attempt/result records and result readback support completion evidence. No generic Kanban/delegation consumer, external destination, human assignment, scheduled work or production workflow is dispatched. This is substantive constrained work-product execution, not full business-task automation.

## Deterministic boundaries

Exact existing trusted Jeff principal, hermesdiscord home, bot, guild and request room remain required; existing recipient attestation permits Jeff's verified private DM only. Existing V1 slash/component/alternate and general-agent deny boundaries remain unchanged. Suneet and other profiles do not gain or lose authority. Jeff is NOT added to an owner allowlist.

V2 runs only when existing `discord.reside_scoped_ops` is true AND `discord.reside_scoped_ops_v2` is literally true in hermesdiscord configuration. Absent V2 uses V1. No unrestricted AIAgent, tool registry, discovery, memory, session history/backfill, shell, browser, general SQL/network or arbitrary delegation. Input/model output cannot choose principal, grants, resources, paths, worker, tools or destination. The model has no tool capability. Lexical denial is convenience, not the authority boundary.

Safe data: static reviewed projection in gateway/reside_corpus.py; raw mixed/sensitive canonical sources are not opened at runtime. Operator-only source paths, line spans and checked digests are in jeff-reside-v2-provenance.json. Snapshot date 2026-10-15 is shown in answers; source drift requires reviewed code changes, never automatic corpus broadening.

Credential provenance: Monday read-only operation uses only hermesdiscord/.env MONDAY_API_TOKEN (no ambient launcher or sibling fallback). Stateless model uses the explicitly pinned existing shared subscription entry at /Users/suneetcowork/.hermes/auth.json providers.openai-codex.tokens.access_token and official Codex endpoint, model gpt-6.1-sol. It does not resolve a pool/provider fallback, change subscription, refresh credentials or enable paid tiers. Missing/invalid authority fails; no secret values enter model messages. Operator-owned token refresh must keep that approved store current.

No client/contact/participant/recap records or CRM exports; no BSHG/personal/personnel/finance access. No spending, permissions/credential changes, sends/publication/enrollment, human-field overwrites, permanent deletion or production writes.

## Tasks, limits and failure

`my tasks` returns up to ten scoped task IDs. `task status <UUID>` reads same-policy private completion evidence without source/model calls. `retry task <UUID>` permits one explicit same-policy retry after an initial attempt. No automatic replay or wider worker escalation. Duplicate event replay uses the original task before any model/source work; changed text or attenuated-policy drift rejects. Invalid result files reject. Task root uses no-follow owned private directories, 0600 exclusive append-only files, fsync and readback. Storage refuses NEW tasks at 1000 directory entries; reads/replay/recovery remain available. Operator archives old data reversibly; no automatic deletion.

One process-wide synthesis at a time, 3-second request interval, 3500-character input, 6000-character model body, bounded private result and 1800-character DM chunks, no mentions or embeds. Model calls have deadlines and no retries; worker failures persist sanitized receipts and never claim completed work. Live-status source failure permits labeled corpus-only work, disallows citing unavailable status, and returns unavailable for status/health questions. Factual/prose quality remains generated and requires human review; deterministic source isolation does not prove every sentence true.

Known recovery limit: a process crash after request persistence but before initial attempt claim leaves a request needing operator intervention (explicit retry requires initial claim). No completion is claimed. Policy version changes intentionally invalidate old-policy requests; preserve their files for operator inspection, never expand their grants.

## Reviewer reproduction

Candidate worktree: /Users/suneetcowork/.hermes/kanban/workspaces/t_fee659a4/scoped-v2
Branch: security/jeff-reside-v2-t_fee659a4, based on 69d0ccf482acff8b385a977c1c2974cc1649f297.

Workspace `run_v2_tests.py` runs 17 selected authorization/privacy/transport/resource/task regression files using the existing production Python, real Discord/OpenAI preload, synthetic home and no cache provider. It narrowly permits the interpreter's relocated stdlib traceback prefix in the existing home-I/O guard, not application state access. `v2_live_probe.py` calls real fixed read-only Monday and the existing Codex subscription with synthetic local task storage: lookup, report, contextual draft, report worker, draft worker, forbidden cross-business request. It creates no Discord events/sends and is NOT a Jeff live canary. `verify_v2_sources.py` checks all four canonical source digests.

Mandatory Marshal review must assess BOTH security and capability sufficiency against the latest task comments, not merely approve parser tests. Evidence package is under /Users/suneetcowork/Suneet's Organized Files/From Claude/Hermes/Jeff Reside Access/.

## Minimal operator rollout — AFTER Marshal approval only

Letterman/Suneet owns integration, profile configuration and gateway lifecycle. Worker did not edit production, change flags, restart, or send canaries.

1. Inspect current production HEAD, dirty/untracked work and concurrent adapter edits; preserve t_78f1c1e9 and recipient/reliability fixes. Integrate the reviewed V2 branch only. Fast-forward only if still valid; never force/reset concurrent work. Adapter is a collision hotspot. Re-run selected suite on integrated exact head.
2. Back up hermesdiscord config reversibly; leave DISCORD_ALLOWED_USERS/owner allowlists and all other profiles untouched. Preserve existing reside_scoped_ops true and add reside_scoped_ops_v2: true only under hermesdiscord's discord settings, using the normal config loader. Confirm exact feature flags, profile home, policy/corpus digests, approved source and credential provenance before any live model request.
3. In an operator-authorized window perform one graceful reload, not a worker restart. Attest actual loaded adapter, reside_scope, reside_assistant, corpus and policy digest for the new process; disk HEAD or PID alone is not loaded-code proof.
4. Obtain genuine Jeff-origin requests in the approved room and confirm private delivery only to his attested DM. Exercise help plus lookup/report/draft and one constrained work-product task; record genuine event IDs/private-delivery evidence without exposing content publicly. Never impersonate Jeff or synthesize a live transport request. No external contacts or sends as canaries.
5. Return exact-head loaded-policy and genuine canary evidence to Marshal on the SAME task. The overall enablement goal is not Done until these gates pass. Approval is not authority expansion.

## Rollback

Operator sets hermesdiscord reside_scoped_ops_v2 false (or restores its pre-V2 config backup), then performs one authorized graceful reload. Existing bounded V1 and deny guards remain. For full scoped-disable, set existing reside_scoped_ops false while preserving general-agent denial. Retain task/proposal files and all audit evidence; no permanent deletion. Do not revert unrelated concurrent changes or broad owner policies.
