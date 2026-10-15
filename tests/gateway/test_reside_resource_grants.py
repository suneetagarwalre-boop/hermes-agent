"""Acceptance regressions for review round two: business grants and label drift."""
import json
import pytest
from gateway import reside_scope as scope
from tests.gateway.test_reside_scoped_ops import principal, row, data


def approved_rows():
    return [row(resource=rid) for rid in scope.RESOURCES]


@pytest.mark.parametrize('page_number', [0, 1])
@pytest.mark.parametrize('name', ['BSHG-only payroll', 'Suneet personal automation', 'ambiguous task', 'reside-action-item-extractor'])
def test_unapproved_sole_dave_never_grants_even_with_approved_name(principal, page_number, name):
    unrelated = row(resource='99999999999', index=2)
    unrelated['name'] = name
    pages = [approved_rows(), []]
    pages[page_number].append(unrelated)
    def post(query, variables, token):
        if not variables:
            return data(pages[0], 'next')
        return {'next_items_page': {'items': pages[1], 'cursor': None}}
    result = scope.automation_report(principal, 'unused', post)
    assert 'Total in scope: 3' in result and 'OK: 3' in result and 'Error: 0' in result
    assert name not in result


@pytest.mark.parametrize('change', ['rename_bshg', 'rename', 'missing', 'duplicate', 'duplicate_later', 'malformed_id', 'group'])
def test_approved_resource_drift_is_not_a_partial_report(principal, change):
    rows = approved_rows()
    cursor = None
    if change == 'rename_bshg': rows[0]['name'] = 'BSHG-only now'
    if change == 'rename': rows[0]['name'] += '-changed'
    if change == 'missing': rows.pop()
    if change == 'duplicate': rows.append(rows[0])
    if change == 'malformed_id': rows[0]['id'] = int(rows[0]['id'])
    if change == 'duplicate_later': cursor = 'next'
    response = data(rows, cursor)
    if change == 'group': response['boards'][0]['groups'][0]['id'] = 'other'
    def post(query, variables, token):
        if variables:
            return {'next_items_page': {'items': [rows[0]], 'cursor': None}}
        return response
    with pytest.raises((ValueError, TypeError)):
        scope.automation_report(principal, 'unused', post)


@pytest.mark.parametrize('settings', [
    '{"labels":{"0":"Partial","1":"Error","2":"OK","3":"Skipped"}}',
    '{"labels":{"0":"Partial","1":"OK","2":"Error","3":"Skipped","4":"Other"}}',
    '{"labels":{"0":"Partial","1":"OK","2":"Error"}}',
    '{"labels":{"00":"Partial","1":"OK","2":"Error","3":"Skipped"}}',
    '{"labels":{"0":"Partial","1":true,"2":"Error","3":"Skipped"}}',
    '{"labels":{"0":"Partial","1":"OK","1":"OK","2":"Error","3":"Skipped"}}',
    '{"labels":{},"labels":{"0":"Partial","1":"OK","2":"Error","3":"Skipped"}}',
    '{}', 'null', '[]', '"text"', '{broken',
])
def test_label_semantic_or_malformed_drift_fails_closed(principal, settings):
    response = data(approved_rows())
    response['boards'][0]['columns'][1]['settings_str'] = settings
    with pytest.raises((ValueError, TypeError)):
        scope.automation_report(principal, 'unused', lambda *a: response)


def test_column_duplicate_fails_closed(principal):
    response = data(approved_rows())
    response['boards'][0]['columns'].append(response['boards'][0]['columns'][1])
    with pytest.raises(ValueError, match='duplicate column'):
        scope.automation_report(principal, 'unused', lambda *a: response)


def test_fixed_ids_not_agent_assignment_define_grant(principal):
    rows = approved_rows()
    for r in rows:
        r['column_values'] = [c for c in r['column_values'] if c['id'] == scope.STATUS]
    result = scope.automation_report(principal, 'unused', lambda *a: data(rows))
    assert 'Total in scope: 3' in result
    assert all(rid not in result for rid in scope.RESOURCES)
    assert all(name not in result for name in scope.RESOURCES.values())


def test_full_page_boundary_with_unknown_sole_dave(principal):
    assert 'query_params:{ids:[' + ','.join(scope.RESOURCES) + ']}' in scope.FIRST
    assert 'dropdown_mm3cxfke' not in scope.FIRST
    first = [row(resource=str(80000000000 + i), index=2) for i in range(499)]
    first.append(row())
    second = [row(resource=rid) for rid in list(scope.RESOURCES)[1:]]
    def post(query, variables, token):
        if not variables: return data(first, 'opaque')
        return {'next_items_page': {'cursor': None, 'items': second}}
    result = scope.automation_report(principal, 'unused', post)
    assert 'OK: 3' in result and 'Error: 0' in result and 'Total in scope: 3' in result
