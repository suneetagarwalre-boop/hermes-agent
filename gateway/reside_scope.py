"""Closed Reside operation broker. Never enters AIAgent, tool registry or Kanban.

Transport constructs the principal; text cannot select identity, grants, resources,
queries, destinations or executable tools. All business output is private to Jeff.
This intentionally small lane is NOT general Reside CRM access.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

HOME = Path('/Users/suneetcowork/.hermes/profiles/hermesdiscord')
JEFF = '1555599898265522327'
BOT = '1507361939041550517'
GUILD = '1506745934921334804'
ROOM = '1506747928541397032'
BOARD = '18406413029'
WORKSPACE = '5302393'
AGENT = 'dropdown_mm3cxfke'
STATUS = 'color_mm856pmn'
LABELS = {0: 'Partial', 1: 'OK', 2: 'Error', 3: 'Skipped', None: 'Unrecorded'}
# Reviewed resource grants, verified by independent item descriptions and exact
# board/group membership. Agent assignment and board branding confer no grant.
# See docs/jeff-reside-scoped-ops.md for discovery evidence and exclusions.
RESOURCES = {
    '11790940111': 'reside-action-item-extractor',
    '11997188711': 'reside-meeting-attendance-mini',
    '11997844820': 'reside-agent-training-attendance-mini',
}
APPROVED_STATUS_LABELS = {str(k): v for k, v in LABELS.items() if k is not None}


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result
DENIED = 'Not available in your scoped Reside lane. Ask Suneet for anything outside the supported operations; nothing was executed.'
HELP = ('Scoped Reside operations (private replies only):\n'
        'reside report — recorded run-status counts for three pinned Reside operational automations.\n'
        'reside lookup automation-health — same bounded operational lookup.\n'
        'reside draft {"objective":"...","context":"..."} — internal SOP working draft from only your supplied facts.\n'
        'reside coordinate {"title":"...","details":"..."} — inert proposal receipt, NOT worker dispatch or approval.\n'
        'No general tools, CRM/contact exports, personnel/financial access, other businesses, sends, spending, account changes, or status writes. No history or attachments are read.')


@dataclass(frozen=True)
class Principal:
    home: Path
    user: str
    bot: str
    guild: str
    channel: str
    is_bot: bool

    def authorize(self) -> None:
        if (self.home != HOME or self.user != JEFF or self.bot != BOT
                or self.guild != GUILD or self.channel != ROOM or self.is_bot):
            raise PermissionError('principal denied')


def restricted(home: Path | None, user: str) -> bool:
    # Independent of feature enablement, pairing, role or allow-all configuration.
    return home == HOME and user == JEFF


def parse_command(text: str) -> tuple[str, dict]:
    if not isinstance(text, str) or len(text) > 1500:
        raise ValueError('invalid command')
    if text in ('reside help', 'reside'):
        return 'help', {}
    if text in ('reside report', 'reside lookup automation-health'):
        return 'report', {}
    for prefix, fields in [('reside draft ', {'objective', 'context'}),
                           ('reside coordinate ', {'title', 'details'})]:
        if text.startswith(prefix):
            # Reject duplicate keys rather than silently accepting a changed grant.
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('duplicate field')
                    result[key] = value
                return result
            payload = json.loads(text[len(prefix):], object_pairs_hook=unique)
            if (not isinstance(payload, dict) or set(payload) != fields
                    or any(not isinstance(v, str) or not v.strip() or len(v) > 600
                           or any(ord(c) < 32 and c != '\n' for c in v)
                           for v in payload.values())):
                raise ValueError('invalid fields')
            return ('draft' if 'objective' in fields else 'coordinate'), payload
    raise ValueError('unsupported operation')


def _post(query: str, variables: dict, token: str) -> dict:
    if not token:
        raise PermissionError('no scoped credential')
    request = urllib.request.Request('https://api.monday.com/v2',
        data=json.dumps({'query': query, 'variables': variables}).encode(),
        headers={'Authorization': token, 'Content-Type': 'application/json', 'API-Version': '2026-07'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise PermissionError('redirect denied')
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=20) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('oversized response')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('errors') or not isinstance(data.get('data'), dict):
        raise ValueError('upstream error')
    return data['data']


# Only immutable IDs, identity-drift sentinels and recorded statuses are read.
# Names are validated internally, never rendered. Unknown IDs are not grants.
FIELDS = ('id name column_values(ids:["color_mm856pmn"]) '
          '{id ... on StatusValue {index}}')
FIRST = ('{boards(ids:[18406413029]){id workspace_id columns{id title type settings_str} '
         'groups(ids:["topics"]){id items_page(limit:500,query_params:{ids:['
         + ','.join(RESOURCES) + ']}){cursor items{' + FIELDS + '}}}}}')
NEXT = ('query($cursor:String!){next_items_page(limit:500,cursor:$cursor){cursor items{' + FIELDS + '}}}')


def automation_report(principal: Principal, token: str, post=_post) -> str:
    principal.authorize()
    data = post(FIRST, {}, token)
    boards = data['boards']
    if len(boards) != 1:
        raise ValueError('board missing')
    board = boards[0]
    if board['id'] != BOARD or str(board['workspace_id']) != WORKSPACE:
        raise PermissionError('resource mismatch')
    schema = {c['id']: c for c in board['columns']}
    if len(schema) != len(board['columns']):
        raise ValueError('duplicate column')
    status = schema.get(STATUS, {})
    if (status.get('title'), status.get('type')) != ('Last Status', 'status'):
        raise ValueError('schema drift')
    settings = json.loads(status['settings_str'], object_pairs_hook=_unique_json)
    if not isinstance(settings, dict) or settings.get('labels') != APPROVED_STATUS_LABELS:
        raise ValueError('status label drift')
    if len(board['groups']) != 1 or board['groups'][0]['id'] != 'topics':
        raise ValueError('group mismatch')
    page = board['groups'][0]['items_page']
    counts = {label: 0 for label in LABELS.values()}
    cursors = set()
    seen = set()
    for _ in range(20):
        items = page['items']
        if not isinstance(items, list) or len(items) > 500:
            raise ValueError('invalid page')
        for item in items:
            item_id = item['id']
            if not isinstance(item_id, str):
                raise ValueError('invalid resource identity')
            if item_id not in RESOURCES:
                continue
            if item_id in seen or item['name'] != RESOURCES[item_id]:
                raise ValueError('resource identity drift')
            seen.add(item_id)
            cols = {c['id']: c for c in item['column_values']}
            if len(cols) != len(item['column_values']):
                raise ValueError('duplicate value')
            index = cols[STATUS]['index']
            if index is not None and type(index) is not int:
                raise ValueError('invalid status')
            if index not in LABELS:
                raise ValueError('status drift')
            counts[LABELS[index]] += 1
        cursor = page['cursor']
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in cursors:
            raise ValueError('invalid pagination')
        cursors.add(cursor)
        page = post(NEXT, {'cursor': cursor}, token)['next_items_page']
    else:
        raise ValueError('incomplete report')
    if seen != set(RESOURCES):
        raise ValueError('approved resource missing; incomplete report')
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    return ('Reside automation run-status report\nAs of ' + stamp + '\n'
            'Scope: three pinned Reside operational automation IDs; not all Hub rows.\n'
            + '\n'.join(f'{k}: {v}' for k, v in counts.items())
            + f'\nTotal in scope: {sum(counts.values())}\n'
            'These are recorded Last Status values, not proof of current live service health. '
            'Blank status means unrecorded, not failed. No human judgments changed.')


def draft(principal: Principal, payload: dict) -> str:
    principal.authorize()
    # Render data only. No model, instruction following, interpolation as code,
    # memory, private source retrieval, external recipient or dispatch path.
    objective = payload['objective'].replace('@', '@\u200b')
    context = payload['context'].replace('@', '@\u200b')
    return ('Reside internal SOP — WORKING DRAFT, not approved or sent\n'
            f'Objective (requester supplied): {objective}\n'
            f'Context (requester supplied; unverified): {context}\n\n'
            '1. Confirm the Reside-only scope, accountable owner and success criteria.\n'
            '2. Validate inputs against current authorized operational records.\n'
            '3. Prepare the internal work product; preserve existing human judgments.\n'
            '4. Escalate spending, external sends, permissions or sensitive access to Suneet.\n'
            '5. Verify the result and record evidence before marking work complete.\n'
            'Open decisions: named owner, due date, acceptance criteria and approval if required.')


def coordinate(principal: Principal, payload: dict) -> str:
    principal.authorize()
    # Deliberately NOT Kanban tasks, cron, or an outbox. No poller or worker reads
    # this table. Promotion requires a new owner-authorized request, not review
    # approval of arbitrary proposal text. Hard-coded location, no user paths.
    folder = principal.home / 'reside-scoped'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = folder / 'proposals.sqlite3'
    if folder.is_symlink() or db.is_symlink():
        raise PermissionError('symlink denied')
    with sqlite3.connect(db, timeout=3) as conn:
        db.chmod(0o600)
        conn.execute('CREATE TABLE IF NOT EXISTS proposals (receipt TEXT PRIMARY KEY, principal TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL)')
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT COUNT(*) FROM proposals').fetchone()[0] >= 200:
            raise ValueError('queue full; owner review needed')
        receipt = str(uuid4())
        conn.execute('INSERT INTO proposals VALUES (?,?,?,?)', (receipt, JEFF,
            json.dumps({'kind': 'untrusted_reside_internal_proposal', 'title': payload['title'],
                        'details': payload['details'], 'capabilities': [], 'dispatchable': False}),
            datetime.now(timezone.utc).isoformat()))
        stored = conn.execute('SELECT payload FROM proposals WHERE receipt=? AND principal=?', (receipt, JEFF)).fetchone()
        if stored is None:
            raise RuntimeError('receipt verification failed')
    return (f'Internal Reside proposal recorded: {receipt}\n'
            'Inert intake only. No task assigned, no agent/worker dispatched, no message sent to another party, '
            'no status changed, and no approval granted. Suneet must authorize any subsequent execution separately.')
