# Jeff Reside scoped broker (staged v1)

This is a deliberately narrow, model-free operation broker, not an owner allowlist
entry, per-toolset role or a general-purpose AI agent for Jeff. Default: disabled.
Only the hermesdiscord profile is affected. It does not change other agents' roles.

## Principals and entry point

Trusted transport: profile home `/Users/suneetcowork/.hermes/profiles/hermesdiscord`,
human Jeff `1555599898265522327`, bot `1507361939041550517`,
guild `1506745934921334804`, exact channel `1506747928541397032`.
Only live human prefix mentions at that exact destination are processed. No
parent-thread inheritance, quoted-author authority, embeds, attachments, snapshots,
reply-history expansion, backfill replay or ambient operation processing.

The Discord adapter intercepts this principal before ordinary admission. Normal
agent admission, direct/batch handler entry, recovered messages, slash commands
and components are denied, even if a future allow-all/role/pairing grant is present.
The general gateway authorization boundary also denies the principal in the
hermesdiscord transport before chat/role/global grants. No AIAgent is constructed;
there is no owner prompt, memory, skill, filesystem/search/browser/shell access,
tool discovery, execute_code, connector batching, delegation or Kanban creation.
This avoids needing to pretend toolset names are a resource capability ceiling.

## Supported commands

Prefix each command with a real mention of Hermes in the exact command-center
channel. All substantive output goes only to Jeff's attested DM recipient. A DM
must be resolvable before source reads or proposal writes. No room business data,
private-output fallback to a room, selectable destination or third-party ping.

- `reside help`: capability and syntax help.
- `reside report`: live read-only recorded automation run-status histogram.
- `reside lookup automation-health`: alias of that same bounded lookup.
- `reside draft {"objective":"...","context":"..."}`: a deterministic internal SOP
  working draft using only these user-provided facts, explicitly unverified. This
  is a structured template, NOT unrestricted natural-language/model drafting.
- `reside coordinate {"title":"...","details":"..."}`: stores an inert proposal,
  with receipt, in profile-local `reside-scoped/proposals.sqlite3`. It does NOT
  create a Kanban card, notify someone, delegate, assign a task or run a worker.
  Stored payload has `capabilities: []`, `dispatchable: false` and an untrusted
  proposal kind. There is no dispatcher/poller consumer. Subsequent execution
  requires a new scoped owner-approved request, not merely an approving review.

Draft/intake JSON fields must match exactly: no routing, principal, recipient,
assignee, tool, contract, file path or grant fields; no duplicate keys or batches.
Quoted instructions inside supplied text are literal data, never executed.
Maximum input 1500 characters, each required nonempty string at most 600.
Queue has a 200-proposal bound and private directory/database modes (0700/0600).
A failed response is not retried automatically. A lost proposal receipt may
represent a committed inert proposal; it is not permission to rerun any work.

## Exact read scope / projection

Discovery used the live Canonical Reference — AI Brain board `18413023224` and
board metadata, not label inference alone. Reside Automations Hub is board
`18406413029`, workspace `5302393` (RESIDE Workspace). It contains mixed-domain
records, so a whole-board export is NOT authorized.

The fixed backend query requests group `topics` (Scheduled Automations) and ONLY
Agent dropdown (`dropdown_mm3cxfke`) plus Last Status (`color_mm856pmn`). It does
not request names, record IDs, descriptions, schedules, free text, URLs, contacts,
personnel, financial data or connected-board fields. Schema/workspace/group IDs
are verified. Only sole assignment `[{id: "1", label: "dave"}]` contributes to the
histogram; unassigned, other-agent and co-assigned records do not. No row or
upstream label/text is returned. Fixed status indices map to Partial/OK/Error/
Skipped/Unrecorded. Unknown status/schema errors fail closed. Blank is unrecorded,
not failure; this is not runtime liveness certification. Paging is bounded, loop
checked and complete or fails; no misleading partial total is returned.

Credential is the already-configured profile-scoped `MONDAY_API_TOKEN`. No new
credential, scope, permission or secret-file edit is required. URL is fixed HTTPS
api.monday.com/v2; redirects, upstream errors, oversize bodies and schema drift
are denied. No model/user-supplied query, resource ID, variables, endpoint or token.

## Explicitly unsupported / denied

General free-form operational lookups/reports, CRM/GHL/contact data and exports,
client records, personal/BSHG data, sensitive personnel/finance, calendar changes,
spending, sends/publication/enrollment, permission/credential/admin changes,
Monday writes or judgment changes, deletions, shell/browser/tool discovery,
delegation and privileged durable work. This initial broker is NOT the full
range of all Reside operations that Suneet may eventually choose to authorize.
No blocker or assertion of impossibility is claimed for later bounded expansion.

## Gates and operator-only rollout

Implementation is isolated on `security/jeff-reside-t_fee659a4`; production source,
allowlists, credentials, config and gateway processes have not been changed.
`discord.reside_scoped_ops` defaults off and only literal YAML boolean true enables
the broker. The principal remains denied at the general-agent boundary regardless
of that flag. Suneet's existing owner admission is preserved.

1. Marshal independently reviews actual staged implementation and behavioral tests.
2. After approval only, Letterman/Suneet integrates the reviewed commit against
   current production HEAD, resolving any adapter/authz collisions deliberately.
   Re-run the scoped and multiplex/owner/recipient regression invocation.
3. The operator enables `discord.reside_scoped_ops: true` only in hermesdiscord's
   config, keeps DISCORD_ALLOWED_USERS owner-only, and controls shared lifecycle.
   This worker MUST NOT reload/restart/kickstart the shared gateway.
4. Verify the actual running deployment, not a bot's commit claim or disk HEAD.
   Review post-start module/policy evidence and obtain a genuine Jeff prefix
   `reside help` and draft canary. Confirm private recipient and no shared output;
   Jeff may then request the bounded report. No one may impersonate his event.
   Do not use external-party sends or real sensitive data as a security canary.
5. If necessary, turn the feature flag off and have the operator reload in their
   normal authorized window. Keep the deny guards. Never delete proposal data or
   revert unrelated reliability/source work. Archive/export only if separately
   authorized. Removing the commit is not a first-line rollback: it also removes
   the fail-closed guard.

Until the operator/genuine-user gates run, state is STAGED, NOT LIVE VERIFIED.
If DMs are disabled, no private business output is disclosed in the public room.
Failures are logged only by operation/message state, without raw API errors,
credentials, user payload or source records. The broker does not promise a shared
room acknowledgment on every failed request.
