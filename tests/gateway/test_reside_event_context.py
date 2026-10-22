"""Event/date intersection and fresh bounded followups (Marshal V3 corrections)."""
import copy
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from gateway import reside_business as business
from gateway import reside_v3 as app
from tests.gateway.test_reside_v3 import principal, source, EVENT, QUESTION


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(business, 'now', lambda: '2026-10-22T20:00:00+00:00')


def registrants(source):
    return [path for path, _ in source.calls if path.endswith('/registrants')]


@pytest.mark.parametrize('selector', ['Stop Chasing Leads Build the Team', '85284217825'])
@pytest.mark.parametrize('date', ['today', 'tomorrow', 'October 22', '2026-10-22'])
def test_all_event_and_date_constraints_before_totals(principal, source, selector, date):
    source.row['start_time'] = '2026-10-19T17:00:00Z'
    answer = app.registration_answer(principal, f'How many registrations for webinar {selector} {date}?', source)
    assert 'approved registrations' not in answer
    assert not registrants(source)


@pytest.mark.parametrize('query', [
    'webinar today', 'webinar October 22', 'webinar 2026-10-22',
    'webinar Stop Chasing Leads Build the Team',
    'webinar 85284217825 today',
    'webinar Stop Chasing Leads Build the Team October 22 2026',
])
def test_ordinary_and_date_only_matching(principal, source, query):
    answer = app.registration_answer(principal, 'How many registrations for ' + query + '?', source)
    assert '92 approved registrations' in answer
    assert len(registrants(source)) == 3


@pytest.mark.parametrize('date', ['today tomorrow', 'today October 23', '2026-10-22 2026-10-23', 'February 30', 'October 22 2025'])
def test_conflicting_or_invalid_dates_clarify(principal, source, date):
    answer = app.registration_answer(principal, f'How many registrations for webinar 85284217825 {date}?', source)
    assert 'approved registrations' not in answer
    assert not registrants(source)


def test_business_timezone_and_boundary(principal, source, monkeypatch):
    monkeypatch.setattr(business, 'now', lambda: '2026-10-23T01:00:00+00:00')
    source.row['start_time'] = '2026-10-23T02:00:00Z'  # still Oct22 PDT
    assert '92 approved registrations' in app.registration_answer(principal, 'webinar 85284217825 today', source)
    source.calls.clear()
    assert 'approved registrations' not in app.registration_answer(principal, 'webinar 85284217825 tomorrow', source)
    assert not registrants(source)


def test_id_cannot_override_conflicting_title(principal, source):
    other = copy.deepcopy(source.row)
    other.update(id=85284217826, topic='Recruiting Systems Leadership Workshop')
    source.page['webinars'].append(other)
    answer = app.registration_answer(principal, 'How many registrations for Recruiting Systems Leadership Workshop webinar 85284217825 today?', source)
    assert 'approved registrations' not in answer
    assert not registrants(source)


def test_ambiguous_title_and_date_do_not_choose_arbitrary_event(principal, source):
    other = copy.deepcopy(source.row)
    other['id'] = 85284217826
    source.page['webinars'].append(other)
    answer = app.registration_answer(principal, 'webinar Stop Chasing Leads Build the Team today', source)
    assert 'approved registrations' not in answer
    assert not registrants(source)


def first_query(principal, source):
    app.claim(principal, EVENT, QUESTION)
    result = app.execute(principal, EVENT, client=source)
    assert '92 approved registrations' in result['body']
    source.calls.clear()
    original = source.zoom
    def changed(path, params=None):
        value = original(path, params)
        if path.endswith('/registrants') and params['status'] == 'approved':
            value['total_records'] = 107
        return value
    source.zoom = changed


@pytest.mark.parametrize('followup', ['How many now?', 'And now?', 'What is the registration count now?'])
def test_followup_uses_fresh_broker_not_old_count_or_model(principal, source, followup):
    first_query(principal, source)
    app.claim(principal, str(int(EVENT) + 1), followup)
    model = Mock(side_effect=AssertionError('aggregate must not enter model'))
    result = app.execute(principal, str(int(EVENT) + 1), client=source, complete=model)
    assert result['state'] == 'complete'
    assert '107 approved registrations' in result['body']
    assert '92 approved registrations' not in result['body']
    assert len(registrants(source)) == 3
    model.assert_not_called()
    assert all('/webinars/85284217825/registrants' == path for path in registrants(source))


@pytest.mark.parametrize('context', ['other_room', 'thread', 'expired', 'future', 'policy', 'no_history', 'intervening_clarification'])
def test_followup_context_is_principal_channel_policy_time_bound(principal, source, monkeypatch, context):
    if context != 'no_history':
        first_query(principal, source)
    if context == 'other_room':
        principal = replace(principal, channel='1562553216045158523')
    elif context == 'thread':
        principal = replace(principal, channel='1562641484979900500', parent=principal.channel)
    elif context == 'expired':
        monkeypatch.setattr(business, 'now', lambda: '2026-10-22T20:05:01+00:00')
    elif context == 'future':
        monkeypatch.setattr(business, 'now', lambda: '2026-10-22T19:59:59+00:00')
    elif context == 'policy':
        monkeypatch.setattr(app, 'POLICY_DIGEST', 'new-policy')
    elif context == 'intervening_clarification':
        app.claim(principal, str(int(EVENT) + 1), 'How many registrations for webinar 99999999999?')
        assert 'approved registrations' not in app.execute(principal, str(int(EVENT) + 1), client=source)['body']
        source.calls.clear()
    event = str(int(EVENT) + 2)
    app.claim(principal, event, 'How many now?')
    model = Mock(side_effect=AssertionError('ambiguous followup must clarify'))
    result = app.execute(principal, event, client=source, complete=model)
    assert result['state'] == 'complete'
    assert 'approved registrations' not in result['body']
    assert 'webinar' in result['body'].lower()
    assert not registrants(source)
    model.assert_not_called()


def test_followup_cannot_widen_id_account_or_bypass_new_date(principal, source):
    first_query(principal, source)
    for offset, text in enumerate(['How many now for webinar 99999999999?', 'How many registrations for webinar 85284217825 tomorrow?'], 1):
        event = str(int(EVENT) + offset)
        app.claim(principal, event, text)
        result = app.execute(principal, event, client=source, complete=Mock(side_effect=AssertionError('model')))
        assert 'approved registrations' not in result['body']
        assert not registrants(source)
    source.host['account_id'] = 'personal-account'
    event = str(int(EVENT) + 4)
    app.claim(principal, event, QUESTION)
    assert app.execute(principal, event, client=source)['state'] == 'error'
    assert not registrants(source)


@pytest.fixture
def two_events(source):
    source.row['start_time'] = '2026-10-19T17:00:00Z'
    other = copy.deepcopy(source.row)
    other.update(id=85284217826, topic='Recruiting Systems Leadership Workshop',
                 start_time='2026-10-22T17:00:00Z')
    source.page['webinars'].append(other)
    original = source.zoom
    def zoom(path, params=None):
        if path == '/webinars/85284217826':
            source.calls.append((path, params))
            return copy.deepcopy(other)
        return original(path, params)
    source.zoom = zoom
    return source


@pytest.mark.parametrize('followup', [
    'How many now today?',
    'How many now October 22?',
    'How many now 2026-10-22?',
    'How many now for Recruiting Systems Leadership Workshop?',
    'How many now for webinar 85284217826 today?',
])
def test_contextual_selectors_cannot_switch_selected_event(principal, two_events, followup):
    source = two_events
    app.claim(principal, EVENT, 'How many registrations for webinar 85284217825 October 19?')
    first = app.execute(principal, EVENT, client=source)
    assert first['state'] == 'complete'
    assert first['aggregate']['id'] == '85284217825'
    source.calls.clear()
    event = str(int(EVENT) + 1)
    app.claim(principal, event, followup)
    model = Mock(side_effect=AssertionError('contextual aggregate must not enter model'))
    result = app.execute(principal, event, client=source, complete=model)
    assert result['state'] == 'complete'
    assert 'Which Reside webinar/date?' in result['body']
    assert result['aggregate'] is None
    assert not registrants(source)
    model.assert_not_called()


@pytest.mark.parametrize('question,selected', [
    ('How many now October 19?', '85284217825'),
    ('How many registrations for webinar 85284217826 today?', '85284217826'),
])
def test_matching_contextual_date_and_explicit_new_query_controls(principal, two_events, question, selected):
    source = two_events
    app.claim(principal, EVENT, 'How many registrations for webinar 85284217825 October 19?')
    assert app.execute(principal, EVENT, client=source)['aggregate']['id'] == '85284217825'
    source.calls.clear()
    event = str(int(EVENT) + 1)
    app.claim(principal, event, question)
    model = Mock(side_effect=AssertionError('aggregate must not enter model'))
    result = app.execute(principal, event, client=source, complete=model)
    assert result['state'] == 'complete'
    assert '92 approved registrations' in result['body']
    assert result['aggregate']['id'] == selected
    assert registrants(source) == [f'/webinars/{selected}/registrants'] * 3
    model.assert_not_called()


@pytest.mark.parametrize('status', ['approved', 'pending', 'denied'])
@pytest.mark.parametrize('date', ['today', 'tomorrow'])
def test_status_qualified_id_queries_still_intersect_dates(principal, source, status, date):
    question = f'How many {status} registrations for webinar 85284217825 {date}?'
    app.claim(principal, EVENT, question)
    model = Mock(side_effect=AssertionError('status aggregate must not enter model'))
    result = app.execute(principal, EVENT, client=source, complete=model)
    assert result['state'] == 'complete'
    if date == 'today':
        assert '92 approved registrations' in result['body']
        assert 'Pending: 0; denied: 0' in result['body']
        assert result['aggregate']['id'] == '85284217825'
        assert registrants(source) == ['/webinars/85284217825/registrants'] * 3
    else:
        assert 'Which Reside webinar/date?' in result['body']
        assert result['aggregate'] is None
        assert not registrants(source)
    model.assert_not_called()


def test_detail_schedule_drift_stops_before_totals(principal, source):
    original = source.zoom
    def drift(path, params=None):
        value = original(path, params)
        if path == '/webinars/85284217825':
            value['start_time'] = '2026-10-19T17:00:00Z'
        return value
    source.zoom = drift
    with pytest.raises((PermissionError, ValueError)):
        app.registration_answer(principal, 'webinar 85284217825 today', source)
    assert not registrants(source)
