"""Exact source-list filters for a bounded, verified native Smart List shape."""
from copy import deepcopy
import json
import uuid
from .core import SafetyError
from .native_definitions import parse
from .native_once import planning_access
from .native_reminder_files import validate_id
from .native_reminder_moves import list_scope
from .native_reminder_smart_lists import snapshot as read_view

COVERAGE = 'reviewed_custom_smart_list_filter_and_all_referenced_private_list_scopes'


def ids(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 10:
        raise SafetyError('Select one to ten exact physical list IDs')
    normalized = []
    for value in values:
        validate_id(value);normalized.append(str(uuid.UUID(value)).upper())
    if len(set(normalized)) != len(normalized):
        raise SafetyError('Source list IDs must be distinct')
    return sorted(normalized)


def source_ids(view):
    value = parse(view['definition_json'])
    lists = value.get('lists')
    if (set(value) != {'lists', 'operation'} or value['operation'] != 'and'
            or not isinstance(lists, dict) or set(lists) != {'include', 'exclude'} or lists['exclude'] != []):
        raise SafetyError('Source changes currently require a Lists-only AND Smart List without excluded lists; other criteria are not rewritten')
    return ids(lists['include'])


def encoded(values):
    return json.dumps({'lists': {'include': ids(values), 'exclude': []}, 'operation': 'and'},
                      sort_keys=True, separators=(',', ':'))


def snapshot(adapter, item):
    view = read_view(item['source_id'], item['id'], reader=adapter.reader)
    current = source_ids(view)
    references = item['reference_ids']
    if (not isinstance(references, list) or not 1 <= len(references) <= 20 or references != sorted(set(references))
            or not set(current + ids(item['fields']['list_ids'])) <= set(references)):
        raise SafetyError('Every current and proposed source list needs an exact bounded reference snapshot')
    scopes = [list_scope(adapter, item['source_id'], identity) for identity in references]
    if read_view(item['source_id'], item['id'], reader=adapter.reader) != view:
        raise SafetyError('Smart List changed while reviewing referenced lists')
    return {'view': view, 'scopes': scopes}


def prepare(adapter, change):
    if (not isinstance(change, dict) or set(change) != {'operation', 'source_id', 'id', 'fields'}
            or change.get('operation') != 'set_smart_list_sources'
            or change['source_id'] not in planning_access('reminders')['source_ids']
            or not isinstance(change['fields'], dict) or set(change['fields']) != {'list_ids'}):
        raise SafetyError('Select one exact custom Smart List and its explicit physical list IDs')
    proposed = ids(change['fields']['list_ids'])
    view = read_view(change['source_id'], change['id'], reader=adapter.reader)
    previous = source_ids(view)
    if proposed == previous:
        raise SafetyError('Smart List already includes exactly those lists')
    item = deepcopy(change);item['fields']['list_ids'] = proposed;item['reference_ids'] = sorted(set(previous + proposed))
    item['before'] = snapshot(adapter, item)
    if item['before']['view'] != view:
        raise SafetyError('Smart List definition changed during preparation')
    item.update(fidelity_verified=True, effects={
        'change': 'Replace only the source-list criterion of this exact Lists-only AND custom Smart List.',
        'before_list_ids': previous, 'after_list_ids': proposed,
        'preservation': 'Keep the view title and all underlying Reminders unchanged. No Reminder move, completion, deletion, sharing or ordering change is requested.',
        'scope': 'All old and new references must be selected-source private ordinary nongrocery physical lists. No excluded lists, other criteria, built-in views or cross-account references.',
        'display': 'The view can display a different set of Reminders after its filter changes. Apple controls refresh and synchronization.',
        'representation': 'The supported native JSON is canonicalized; restoring the same source IDs restores the criterion, not its former JSON whitespace or key order.',
        'quota_objects': 1})
    return item


def record_matches(item, observed):
    try:
        before = item['before'];expected = dict(before, view=dict(before['view'], definition_json=encoded(item['fields']['list_ids'])))
        return (item['operation'] == 'set_smart_list_sources' and observed.get('state') == 'custom_smart_list_sources_changed'
                and observed.get('id') == item['id'] and observed.get('source_id') == item['source_id']
                and observed.get('snapshot') == expected)
    except (KeyError, TypeError, AttributeError, ValueError, SafetyError):
        return False


def expected_observation(adapter, item):
    value = {'state': 'custom_smart_list_sources_changed', 'id': item['id'], 'source_id': item['source_id'],
             'snapshot': snapshot(adapter, item)}
    if not record_matches(item, value):
        raise SafetyError('Exact new source filter and unchanged referenced scopes do not verify')
    return value


def verify(adapter, item, outcome):
    if any(outcome.get(key) != item[key] for key in ('id', 'source_id')):
        return False
    expected_observation(adapter, item)
    return True
