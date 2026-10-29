"""Rename one exact custom Smart List without changing its raw native filter."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_reminder_smart_lists import snapshot as read_snapshot

COVERAGE = 'reviewed_custom_smart_list_identity_title_and_raw_filter'


def snapshot(adapter, item):
    return read_snapshot(item['source_id'], item['id'], reader=adapter.reader)


def prepare(adapter, change):
    if (not isinstance(change, dict) or set(change) != {'operation', 'source_id', 'id', 'fields'}
            or change.get('operation') != 'rename_smart_list'
            or change['source_id'] not in planning_access('reminders')['source_ids']
            or not isinstance(change['fields'], dict) or set(change['fields']) != {'title'}):
        raise SafetyError('Select one exact custom Smart List, source and title')
    title = change['fields']['title']
    if (not isinstance(title, str) or not title.strip() or len(title) > 1000
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in title)):
        raise SafetyError('Use one nonempty bounded Smart List title')
    item = deepcopy(change);item['before'] = snapshot(adapter, item)
    if item['before']['title'] == title:
        raise SafetyError('Smart List already has the requested title')
    item.update(fidelity_verified=True, effects={
        'change': 'Rename only this exact custom Smart List view in the selected account.',
        'preservation': 'Preserve its raw native filter bytes. No Reminder, membership, definition, sharing or ordering change is requested.',
        'limits': 'Built-in Smart Lists and physical lists refuse. Complete unexposed metadata and cross-device synchronization are not verified.'})
    return item


def record_matches(item, observed):
    try:
        return (item['operation'] == 'rename_smart_list' and observed.get('state') == 'custom_smart_list_renamed'
                and observed.get('id') == item['id'] and observed.get('source_id') == item['source_id']
                and observed.get('snapshot') == dict(item['before'], title=item['fields']['title']))
    except (KeyError, TypeError, AttributeError):
        return False


def expected_observation(adapter, item):
    value = {'state': 'custom_smart_list_renamed', 'id': item['id'], 'source_id': item['source_id'],
             'snapshot': snapshot(adapter, item)}
    if not record_matches(item, value):
        raise SafetyError('Exact new Smart List title and unchanged raw filter do not verify')
    return value


def verify(adapter, item, outcome):
    if any(outcome.get(key) != item[key] for key in ('id', 'source_id')):
        return False
    expected_observation(adapter, item)
    return True
