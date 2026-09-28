# Local custom reconciliation — 2026-09-27

Base: `origin/main` at `bac0c45d8593ed9d53a8e3fcacecdc920b71a2c4`.

This ledger reconciles the 17 protected commits and the preserved working-tree patch against the current split-module upstream architecture. “Ported” means the custom intent is represented in this branch and has focused regression coverage. “Upstream-equivalent” means current upstream already carries the intent, so no duplicate implementation was added.

## Commit dispositions

| # | Commit | Disposition | Current evidence |
|---|---|---|---|
| 1 | `f72f636b5ab602bb3e070ca5351797e93e5c8c61` plain Discord mentions from live roster | Ported | `plugins/platforms/discord/mention_rewriter.py`, adapter integration, `tests/gateway/test_discord_plain_mention_rewrite.py`. |
| 2 | `881536acec24b212652a0dfe9f14d398087f7327` standalone mention sends | Ported | Same current mention rewriter/adapter surface and standalone-send cases in `test_discord_plain_mention_rewrite.py`. |
| 3 | `c753da8d8844598530d8a25c919349c3163d7db4` requester mentions | Ported | Current inbound split in `gateway/run_inbound.py`; requester IDs are added to shared-message/session context. Covered by `tests/gateway/test_shared_group_sender_prefix.py` and redaction coverage. |
| 4 | `0e15e5f4a338f84497b9f035b8e4a81194533423` hardened gateway health probes | Ported | Current status/server split: `gateway/status.py`, `hermes_cli/web_routers/status.py`, `hermes_cli/web_server_gateway.py`; focused coverage in `tests/gateway/test_gateway_health_probe_hardening.py`. |
| 5 | `4042be7550cb8f42a8eb270e4c822f21d1fc6d5c` separate Discord addressability/authority | Ported | `gateway/session.py`, `gateway/slash_commands.py`, and Discord adapter use separate slash operator authority (`HERMES_SLASH_OPERATOR_IDS`). Covered by Discord gate isolation tests. |
| 6 | `644422114bb07ceea96597ff4458a516003245e7` Discord inter-agent hop cap | Ported | Discord adapter preserves the hop-cap gate in current routing; `tests/plugins/platforms/test_discord_gate_isolation.py`. |
| 7 | `77e0ab5d65947a249ef516f792c1575bcf7242f0` one Discord Kanban completion receipt | Ported | `tools/kanban_tools.py` resolves Discord auto-subscriptions as wake-only; notifier ordering is covered by `tests/gateway/test_kanban_notifier_wake_only_ordering.py`. |
| 8 | `834c105eeddbf1c3c91153e8b43c9d0d55487787` durable Kanban goal contracts | Ported | `hermes_cli/kanban_db.py`, `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`: contract validation, inheritance, scoped completion metadata, deterministic deduplication, review enforcement. Covered by tools and review-provenance tests. |
| 9 | `7f9471f97b7677aafc955d39fadfd7f6c3cda599` one-shot provider failure propagation | Upstream-equivalent | Current implementation is stronger in `hermes_cli/cli_single_query.py`: both `-q` and `-Q` map failed/partial/interrupted/provider outcomes, including transient and terminal Kanban provider codes. Verified by `tests/hermes_cli/test_single_query_exit_contract.py` and `test_single_query_session_finalize.py`. No old monolithic `cli.py` implementation duplicated. |
| 10 | `53be53149125cd49e6f3d6e1312aa8ef18e016de` persisted results and leased Discord receipts | Ported | Summary-only completion persists to `tasks.result`; `hermes_cli/kanban_delivery.py` stores provider-confirmed Discord message IDs; additive schema/migrations live in `kanban_db.py`/`kanban_db_connect.py`; cursor leases live in `kanban_db_notify.py`; notifier confirms exact event delivery. Covered by `tests/hermes_cli/test_kanban_delivery_receipts.py`. |
| 11 | `4a47de1ea0d8bc79572748e097da5f1cc006efce` SQLite statement cache disabling | Ported | Applied to current cross-thread connection constructors in `hermes_state_dbfile.py` and `hermes_cli/kanban_db_connect.py`; `tests/test_sqlite_shared_statement_cache.py`. |
| 12 | `c5063738ea57d83c53f6a40877c041020a08f97d` leased wake survives failure cap | Ported | Notifier retains the subscription at the failure cap, leaves the durable lease for cooldown, and retries after expiry. `tests/gateway/test_kanban_notifier_wake_only_ordering.py`. |
| 13 | `895b7f8d3ac4318965e5636352a0bbe7affe3255` run auto-finalize and review convergence | Ported | `hermes_cli/cli_shutdown.py`, `cli_single_query.py`, `tools/kanban_tools.py`, and `kanban_db.py`: clean one-shot exits close the exact current run; review-required auto-finalize routes to the contracted reviewer; three rejected round trips park with review evidence. Covered by `test_kanban_worker_auto_finalize.py` and `test_kanban_review_convergence_ceiling.py`. |
| 14 | `7fcecceb50041874c08bd664220d5983f729657d` room-scoped Discord triggers/passive context | Ported | Current Discord adapter separates trigger authorization from passive history/context. Covered by `tests/gateway/test_discord_channel_response_policy.py`. |
| 15 | `4881c9f7fe432e7f2f9ee8d1aeef9e9c2f95cd56` cron vault-audit completion proof | Ported | `cron/audit_completion.py` provides bounded per-fire append/read-back/hash receipts; current `cron/scheduler.py` injects the opt-in prompt, validates proof before success, preserves a partial report, and persists `partial`. Covered by `tests/cron/test_audit_completion.py`. Jobs without `vault_audit` are unchanged. |
| 16 | `bc40e430192f89d0f4b43dcb2c08a91d75f8796e` routable-only delivery sweeper | Ported | Hourly notifier GC also audits only provider-confirmable Discord text routes. Unroutable and wake-only subscriptions are excluded rather than producing permanent false warnings. Covered by `test_kanban_delivery_receipts.py`. |
| 17 | `5ed3b316da7362174fdde9a45635a0404f38382e` transactional review-claim authorization | Ported | Completion validates source scope and the exact active review run before artifact work and again under the write lock, using current `_retry_status_for_run` provenance. Covered by `tests/hermes_cli/test_kanban_completion_review_provenance.py`. |

## Preserved working-tree patch

Disposition: **Ported**.

- Risky calendar/CRM/outbound/money/historical/export/multi-system cards require a durable contract and `requires_review=true`; reviewer defaults/stamps to `marshal` (`tools/kanban_tools.py`, schema text, focused tool tests).
- Contract-required review is exposed by `kanban_show()` as `contract` and worker prompt guidance explicitly requires `kanban_request_review(..., reviewer='marshal')` instead of direct completion (`agent/prompt_builder.py`, `tests/agent/test_system_prompt.py`).
- Auto-finalization honors the same rule and routes a clean but lifecycle-less worker exit to Marshal rather than bypassing review.

## Delivery and compatibility notes

- Discord text receipts are provider-confirmed message IDs. Delivery remains intentionally at-least-once: a process death after Discord accepts a message but before SQLite commits its ID can duplicate a retry; missing evidence is never treated as delivered.
- Discord wake-only subscriptions do not return a direct provider message ID and are deliberately excluded from the provider-receipt sweeper. Their durability is established by leased cursor ordering/retry tests instead.
- The vault helper retains its original POSIX-only bounded writer (`fcntl` and `signal.alarm`); this branch targets the current macOS/Linux cron architecture.
- No gateway, profile, external API, live Discord, or real vault canary was run in this protected lane. Acceptance is compile/static/focused-test evidence only.
