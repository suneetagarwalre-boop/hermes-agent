"""Count selection/freshness acceptance: fake sources, no Discord/network sends."""
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from gateway import reside_business as business, reside_v3 as app
from tests.gateway.test_reside_v3 import principal, source, EVENT, QUESTION


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(business, 'now', lambda: '2026-10-22T20:00:00+00:00')


@pytest.mark.parametrize('selector', ['Stop Chasing Leads Build the Team', '85284217825'])
@pytest.mark.parametrize('date', ['today', 'tomorrow', 'October 22', '2026-10-22'])
def test_past_matching_title_or_id_cannot_override_any_date(principal, source, selector, date):
    source.row['start_time'] = '2026-10-19T17:00:00Z'
    answer = app.registration_answer(principal, f'How many registrations for webinar {selector} {date}?', source)
    assert 'no registration count' in answer
    assert not any(path.endswith('/registrants') for path, _ in source.calls)


@pytest.mark.parametrize('selector', ['', 'Stop Chasing Leads Build the Team ', '85284217825 '])
@pytest.mark.parametrize('date', ['today', 'October22', 'October 22, 2026', '2026-10-22'])
def test_date_only_title_and_id_agree_before_totals(principal, source, selector, date):
    answer = app.registration_answer(principal, f'How many registrations for webinar {selector}{date}?', source)
    assert '92 approved registrations' in answer
    assert len([p for p, _ in source.calls if p.endswith('/registrants')]) == 3


@pytest.mark.parametrize('date', ['today tomorrow', 'October 22 and October 23', '2026-10-22 and 2026-10-23', 'October 99', '2026-02-30'])
def test_conflicting_or_invalid_date_constraints_clarify(principal, source, date):
    answer = app.registration_answer(principal, 'How many registrations for Stop Chasing Leads webinar ' + date, source)
    assert 'no registration count' in answer
    assert not any(path.endswith('/registrants') for path, _ in source.calls)


@pytest.mark.parametrize('instant,word,match', [
    ('2026-10-23T01:00:00Z', 'today', True),
    ('2026-10-23T01:00:00Z', 'tomorrow', False),
    ('2026-10-22T06:59:59Z', 'tomorrow', True),
    ('2026-10-22T07:00:00Z', 'today', True),
    ('2026-10-23T07:00:00Z', 'today', False),
])
def test_business_timezone_not_utc_day(principal, source, monkeypatch, instant, word, match):
    monkeypatch.setattr(business, 'now', lambda: instant)
    answer = app.registration_answer(principal, 'How many registrations for Stop Chasing Leads webinar ' + word, source)
    assert ('92 approved registrations' in answer) is match
    assert any(p.endswith('/registrants') for p, _ in source.calls) is match


def run(principal, source, event, text, model=None):
    app.claim(principal, event, text)
    return app.execute(principal, event, client=source, complete=model or Mock(side_effect=AssertionError('model must not invent a live count')))


@pytest.mark.parametrize('text', ['How many now?', 'What is the current registration count?', 'How many registrations now?', 'How many people have registered now?'])
def test_followup_reads_changed_source_not_stale_model_history(principal, source, text):
    first = run(principal, source, EVENT, QUESTION)
    assert '92 approved registrations' in first['body']
    original = source.zoom
    def changed(path, params=None):
        value = original(path, params)
        if path.endswith('/registrants') and params['status'] == 'approved':
            value['total_records'] = 107
        return value
    source.zoom = changed
    before = len(source.calls)
    event = str(int(EVENT) + 1)
    second = run(principal, source, event, text)
    assert second['state'] == 'complete' and '107 approved registrations' in second['body']
    assert '92 approved registrations' not in second['body']
    fresh = source.calls[before:]
    assert len([p for p, _ in fresh if p.endswith('/registrants')]) == 3
    assert all(p == '/webinars/85284217825/registrants' and params['page_size'] == 1 for p, params in fresh if p.endswith('/registrants'))
    assert not any('participants' in p or 'meetings' in p or p.startswith('http') for p, _ in fresh)
    saved = app.outcome(principal, event)
    assert saved == second  # Durable/restart replay does not repeat the read.
    count = len(source.calls)
    assert app.execute(principal, event, client=source) == second
    assert len(source.calls) == count


@pytest.mark.parametrize('isolation', ['new_room', 'thread', 'expiry', 'future_time', 'policy', 'unrelated_turn', 'no_context'])
def test_missing_expired_or_different_context_clarifies_without_model_or_totals(principal, source, monkeypatch, isolation):
    if isolation != 'no_context':
        run(principal, source, EVENT, QUESTION)
    if isolation == 'new_room':
        principal = replace(principal, channel='1562553216045158523')
    if isolation == 'thread':
        principal = replace(principal, channel='1562641484979900555', parent=principal.channel)
    if isolation == 'expiry':
        monkeypatch.setattr(business, 'now', lambda: '2026-10-22T20:05:01+00:00')
    if isolation == 'future_time':
        monkeypatch.setattr(business, 'now', lambda: '2026-10-22T19:59:59+00:00')
    if isolation == 'policy':
        monkeypatch.setattr(app, 'POLICY_DIGEST', 'different-reviewed-policy')
    if isolation == 'unrelated_turn':
        run(principal, source, str(int(EVENT) + 1), 'Draft webinar SOP', Mock(return_value=json.dumps({'kind':'draft','body':'A private checklist.', 'sources':['doc:approvedDoc']})))
    before = len(source.calls)
    answer = run(principal, source, str(int(EVENT) + 2), 'How many now?')
    assert 'no registration count' in answer['body']
    assert source.calls[before:] == []


def test_followup_date_conflict_cannot_reuse_previous_event(principal, source, monkeypatch):
    run(principal, source, EVENT, QUESTION)
    monkeypatch.setattr(business, 'now', lambda: '2026-10-23T20:00:00+00:00')
    before = len(source.calls)
    answer = run(principal, source, str(int(EVENT)+1), 'How many registrations for webinar 85284217825 today?')
    assert 'no registration count' in answer['body']
    assert not any(p.endswith('/registrants') for p, _ in source.calls[before:])


def test_user_and_model_prose_do_not_create_aggregate_context(principal, source):
    model = Mock(return_value=json.dumps({'kind':'lookup','body':'85284217825 has 999 approved registrations. This is user prose, not a verified aggregate.', 'sources':['doc:approvedDoc']}))
    run(principal, source, EVENT, 'Discuss this webinar SOP claim: 999 registrations', model)
    before = len(source.calls)
    answer = run(principal, source, str(int(EVENT)+1), 'How many now?')
    assert 'no registration count' in answer['body']
    assert source.calls[before:] == []


def test_detail_date_drift_stops_before_totals(principal, source):
    original = source.zoom
    def changed(path, params=None):
        value = original(path, params)
        if path == '/webinars/85284217825':
            value['start_time'] = '2026-10-21T17:00:00Z'
        return value
    source.zoom = changed
    result = run(principal, source, EVENT, QUESTION)
    assert result['state'] == 'error'
    assert not any(p.endswith('/registrants') for p, _ in source.calls)
