"""Read-only business brokerage. Request prose never becomes a URL/query/account.

No registry, shell, discovery tool, raw contacts, generic SQL or mutable business
API is exposed. OAuth POSTs obtain existing credentials only; business calls GET.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

from gateway import reside_scope as scope

ZOOM_ACCOUNT = 'S6WxwimRRWamvMy4w8NZwg'
ZOOM_HOST = 'ThZAeEz1TtubSKrPMhZezw'
ZOOM_EMAIL = 'webinars@resideplatform.com'
GOOGLE_CREDENTIAL = Path('/Users/suneetcowork/.google_workspace_mcp/credentials/suneet@suneetagarwal.com.json')
CATALOG_PATH = Path(__file__).with_name('reside_documents.json')
# Account/host grants are independent of topic wording or who assigned a task.
# Exact past event remains accessible even after Zoom removes it from upcoming.
KNOWN_WEBINARS = ('85284217825',)
MAX_BYTES = 180_000


def now():
    return datetime.now(UTC).isoformat(timespec='seconds')


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size > 1_000_000:
            raise PermissionError('credential store rejected')
        return json.loads(os.read(fd, 1_000_001), object_pairs_hook=scope._unique_json)
    finally:
        os.close(fd)


class BusinessClient:
    """Only fixed paths selected by server code below. No model-facing request method."""
    def __init__(self, principal):
        principal.authorize()
        from agent.secret_scope import load_env_file
        self.env = load_env_file(principal.home / '.env')
        self._zoom = None
        self._google = None

    def _request(self, method, url, *, headers=None, params=None, data=None, auth=None, raw=False):
        import httpx
        with httpx.Client(timeout=httpx.Timeout(12), follow_redirects=False) as c:
            with c.stream(method, url, headers=headers, params=params, data=data, auth=auth) as r:
                r.raise_for_status()
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('bounded source too large')
                    chunks.append(chunk)
                content = b''.join(chunks)
        return content if raw else json.loads(content, object_pairs_hook=scope._unique_json)

    def zoom(self, path, params=None):
        # Defense even for internal callers: no absolute URLs, alternate hosts,
        # arbitrary endpoints, panelists, reports or participant enumerations.
        if not re.fullmatch(r'/users/webinars%40resideplatform\.com(?:/webinars)?|/webinars/[0-9]{9,12}(?:/registrants)?', path):
            raise PermissionError('Zoom route denied')
        if self._zoom is None:
            if self.env.get('ZOOM_RESIDE_ACCOUNT_ID') != ZOOM_ACCOUNT:
                raise PermissionError('Reside account drift')
            result = self._request('POST', 'https://zoom.us/oauth/token',
                params={'grant_type':'account_credentials','account_id':ZOOM_ACCOUNT},
                auth=(self.env['ZOOM_RESIDE_CLIENT_ID'], self.env['ZOOM_RESIDE_CLIENT_SECRET']))
            self._zoom = result['access_token']
        return self._request('GET', 'https://api.zoom.us/v2' + path,
            headers={'Authorization':'Bearer '+self._zoom}, params=params)

    def google(self, file_id, *, export=False):
        # Lookup outside the immutable reviewed catalog cannot hit the network.
        if file_id not in {r['id'] for r in catalog()}:
            raise PermissionError('document not granted')
        if self._google is None:
            cred = private_json(GOOGLE_CREDENTIAL)
            result = self._request('POST', 'https://oauth2.googleapis.com/token',
                data={k:cred[k] for k in ('client_id','client_secret','refresh_token')}
                    | {'grant_type':'refresh_token'})
            self._google = result['access_token']
        path = 'https://www.googleapis.com/drive/v3/files/' + file_id
        params = {'mimeType':'text/plain'} if export else {
            'fields':'id,name,mimeType,parents,owners(emailAddress),trashed,modifiedTime',
            'supportsAllDrives':'true'}
        return self._request('GET', path + ('/export' if export else ''),
            headers={'Authorization':'Bearer '+self._google}, params=params, raw=export)


def catalog():
    value = json.loads(CATALOG_PATH.read_text(), object_pairs_hook=scope._unique_json)
    if not isinstance(value, list) or len(value) > 100:
        raise PermissionError('invalid resource catalog')
    return value


def webinars(principal, client=None):
    """Bounded complete host catalog, plus pinned historical event; no registrants."""
    principal.authorize()
    client = client or BusinessClient(principal)
    host = client.zoom('/users/webinars%40resideplatform.com')
    if (host.get('id'), host.get('email'), host.get('account_id')) != (ZOOM_HOST,ZOOM_EMAIL,ZOOM_ACCOUNT):
        raise PermissionError('Zoom host drift')
    rows, seen_pages, next_page = {}, set(), ''
    for _ in range(5):
        page = client.zoom('/users/webinars%40resideplatform.com/webinars',
            {'page_size':100, 'next_page_token':next_page})
        for row in page['webinars']:
            wid = str(row['id'])
            if not re.fullmatch(r'[0-9]{9,12}',wid) or row.get('host_id') != ZOOM_HOST or wid in rows:
                raise PermissionError('webinar catalog identity drift')
            rows[wid] = row
        next_page = page.get('next_page_token','')
        if not next_page: break
        if not isinstance(next_page,str) or len(next_page)>4096 or next_page in seen_pages:
            raise ValueError('invalid webinar pagination')
        seen_pages.add(next_page)
    else:
        raise ValueError('webinar catalog incomplete')
    for wid in KNOWN_WEBINARS:
        if wid not in rows:
            rows[wid] = client.zoom('/webinars/'+wid)
    result = []
    for wid,row in rows.items():
        if str(row.get('id')) != wid or row.get('host_id') != ZOOM_HOST:
            raise PermissionError('webinar host mismatch')
        # Never expose join/start URLs, passcodes, host settings or email lists.
        title = row.get('topic')
        if not isinstance(title,str) or not title.strip() or len(title)>300:
            raise ValueError('invalid webinar title')
        result.append({'id':wid,'title':title,'start':row.get('start_time'),
            'occurrences':[{'id':str(o['occurrence_id']),'start':o['start_time']}
                           for o in (row.get('occurrences') or []) if o.get('status')!='deleted']})
    return result


def registration_counts(principal, webinar_id, client=None, *, occurrence=None):
    """Aggregate only. A page of one is required by Zoom's totals endpoint.

    Transport bytes can contain one registrant, but no such fields enter model,
    receipt store, logs or output. Count metadata is the only returned projection.
    """
    principal.authorize()
    client = client or BusinessClient(principal)
    approved = {r['id']:r for r in webinars(principal,client)}
    if webinar_id not in approved:
        raise PermissionError('unknown Reside webinar')
    row = approved[webinar_id]
    if row['occurrences']:
        if occurrence not in {o['id'] for o in row['occurrences']}:
            raise ValueError('specify webinar occurrence')
    elif occurrence is not None:
        raise ValueError('unexpected occurrence')
    result = {'id':webinar_id,'title':row['title'],'start':row['start'],
              'host':ZOOM_EMAIL,'as_of':now(),'source':'Zoom /webinars/{id}/registrants total_records',
              'occurrence':occurrence}
    for status in ('approved','pending','denied'):
        params = {'page_size':1,'status':status}
        if occurrence: params['occurrence_id']=occurrence
        page = client.zoom('/webinars/'+webinar_id+'/registrants',params)
        count = page.get('total_records')
        if type(count) is not int or not 0 <= count <= 10_000_000:
            raise ValueError('invalid registration total')
        result[status] = count
    return result


def documents(principal, question, client=None):
    """Exact reviewed Drive resources, live identity + byte-digest verification.

    Not a name/domain wildcard grant. A changed doc is unavailable pending source
    review, not a demand for Jeff to re-obtain Suneet's existing Reside read grant.
    Return bounded relevant passages; source text is data, never executable policy.
    """
    principal.authorize()
    client = client or BusinessClient(principal)
    terms = set(re.findall(r'[a-z0-9]{3,}', question.lower())) - {
        'the','and','for','what','how','can','you','please','reside','draft','report','task'}
    ranked = sorted(catalog(), key=lambda r:(-len(terms & set(r['keywords'])),r['id']))
    best=max((len(terms & set(r['keywords'])) for r in ranked),default=0)
    chosen = [r for r in ranked if len(terms & set(r['keywords']))>=max(1,(best+1)//2)][:3]
    output, unavailable = [], []
    for grant in chosen:
        try:
            meta = client.google(grant['id'])
            if (meta.get('id')!=grant['id'] or meta.get('name')!=grant['name']
                    or meta.get('mimeType')!='application/vnd.google-apps.document'
                    or meta.get('trashed') is not False
                    or sorted(meta.get('parents',[]))!=sorted(grant['parents'])
                    or sorted(o['emailAddress'] for o in meta.get('owners',[]))!=grant['owners']):
                raise PermissionError('document identity drift')
            raw = client.google(grant['id'],export=True)
            if hashlib.sha256(raw).hexdigest()!=grant['sha256']:
                raise PermissionError('document content requires source review')
            text = raw.decode('utf-8-sig')
            paragraphs = text.replace('\r\n','\n').split('\n')
            # Never transmit the illustrative Zoom passcode line to synthesis.
            paragraphs = [p for p in paragraphs if 'passcode:' not in p.lower()]
            # Reviewed catalog's largest source is ~42k bytes. Keep the whole
            # procedural sequence rather than silently dropping approval steps.
            if len(raw)>48000:raise ValueError('reviewed document size bound')
            text='\n'.join(paragraphs)
            output.append({'id':'doc:'+grant['id'],'title':grant['name'],
                'verified_at':now(),'modified':meta['modifiedTime'],'text':text,
                'excerpt':False,'url':'https://docs.google.com/document/d/'+grant['id']+'/edit'})
        except Exception:
            unavailable.append('doc:'+grant['id'])
    return {'documents':output,'unavailable':unavailable,
            'catalog':[{'id':'doc:'+g['id'],'title':g['name']} for g in catalog()]}
