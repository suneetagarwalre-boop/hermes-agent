"""Common temporal forms must constrain initial and context-bound aggregate reads."""
from unittest.mock import Mock

import pytest

from gateway import reside_business as business
from gateway import reside_v3 as app
from tests.gateway.test_reside_v3 import principal, source, EVENT

TITLE = 'Stop Chasing Leads Build the Team'
ID = '85284217825'


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(business, 'now', lambda: '2026-10-22T20:00:00+00:00')


def execute(principal, source, text, event=EVENT):
    app.claim(principal, event, text)
    model = Mock(side_effect=AssertionError('aggregate/clarification must not enter model'))
    result = app.execute(principal, event, client=source, complete=model)
    model.assert_not_called()
    return result


def totals(source):
    return [(path, params['status']) for path, params in source.calls
            if path.endswith('/registrants')]


def assert_clarification(result, source):
    assert result['state'] == 'complete'
    assert 'Which Reside webinar/date?' in result['body']
    assert 'approved registrations' not in result['body']
    assert result['aggregate'] is None
    assert not totals(source)


@pytest.mark.parametrize('selector', [ID, TITLE])
@pytest.mark.parametrize('qualifier', [
    'yesterday', '10/21/2026', '10/21/2026 today',
    '2026-10-22 10/21/2026', 'October 22 10/21/2026',
    '10/22/2026 yesterday', '10/22/2026 10/21/2026',
])
def test_conflicting_common_dates_never_read_totals(principal, source, selector, qualifier):
    result = execute(principal, source, f'How many registrations for webinar {selector} {qualifier}?')
    assert_clarification(result, source)


@pytest.mark.parametrize('selector', [ID, TITLE])
@pytest.mark.parametrize('qualifier,start', [
    ('10/22/2026', '2026-10-22T17:00:00Z'),
    ('10/22/2026 today October 22 2026-10-22', '2026-10-22T17:00:00Z'),
    ('yesterday', '2026-10-21T17:00:00Z'),
    ('10/21/2026 yesterday', '2026-10-21T17:00:00Z'),
    ('10/22/2025', '2025-10-22T17:00:00Z'),
    ('2/29/2024', '2024-02-29T17:00:00Z'),
])
def test_matching_forms_and_historical_years_read_exact_event(principal, source, selector, qualifier, start):
    source.row['start_time'] = start
    result = execute(principal, source, f'How many registrations for webinar {selector} {qualifier}?')
    assert result['state'] == 'complete'
    assert '92 approved registrations' in result['body']
    assert result['aggregate']['id'] == ID
    assert totals(source) == [(f'/webinars/{ID}/registrants', s)
                              for s in ('approved', 'pending', 'denied')]


@pytest.mark.parametrize('qualifier', [
    '10/22', '22/10/2026', '10/22/26', '2026/10/22',
    '10-22-2026', '10.22.2026', '10/32/2026', '2/29/2025',
    'last Thursday', 'next week', 'day before yesterday',
    'October 2025', 'Oct. 22', '2025',
])
@pytest.mark.parametrize('selector', [ID, TITLE])
def test_unsupported_or_invalid_temporal_residue_clarifies(principal, source, selector, qualifier):
    result = execute(principal, source, f'How many registrations for webinar {selector} {qualifier}?')
    assert_clarification(result, source)


@pytest.mark.parametrize('qualifier', [
    'yesterday', '10/21/2026', 'today 10/21/2026',
    '10/22', 'last Thursday', '10/22/26',
])
def test_followup_dates_cannot_disappear_or_replace_context(principal, source, qualifier):
    first = execute(principal, source, f'How many registrations for webinar {ID} today?')
    assert first['aggregate']['id'] == ID
    source.calls.clear()
    result = execute(principal, source, f'How many now {qualifier}?', str(int(EVENT) + 1))
    assert_clarification(result, source)


@pytest.mark.parametrize('qualifier,start', [
    ('10/22/2026', '2026-10-22T17:00:00Z'),
    ('yesterday', '2026-10-21T17:00:00Z'),
])
def test_matching_followup_reads_all_fresh_statuses(principal, source, qualifier, start):
    source.row['start_time'] = start
    execute(principal, source, f'How many registrations for webinar {ID} {qualifier}?')
    source.calls.clear()
    original = source.zoom

    def changed(path, params=None):
        value = original(path, params)
        if path.endswith('/registrants'):
            value['total_records'] = {'approved': 108, 'pending': 3, 'denied': 1}[params['status']]
        return value

    source.zoom = changed
    result = execute(principal, source, f'How many now {qualifier}?', str(int(EVENT) + 1))
    assert result['state'] == 'complete'
    assert '108 approved registrations' in result['body']
    assert 'Pending: 3; denied: 1' in result['body']
    assert result['aggregate']['id'] == ID
    assert totals(source) == [(f'/webinars/{ID}/registrants', s)
                              for s in ('approved', 'pending', 'denied')]


def test_yesterday_uses_business_day_at_utc_boundary(principal, source, monkeypatch):
    monkeypatch.setattr(business, 'now', lambda: '2026-10-23T01:00:00+00:00')
    source.row['start_time'] = '2026-10-22T02:00:00Z'  # Oct21 in Los Angeles
    result = execute(principal, source, f'How many registrations for webinar {TITLE} yesterday?')
    assert '92 approved registrations' in result['body']
    assert len(totals(source)) == 3
