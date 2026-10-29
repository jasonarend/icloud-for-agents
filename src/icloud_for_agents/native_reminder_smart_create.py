"""Create a bounded native Lists-AND Smart List with an optional Today criterion."""
from copy import deepcopy
import uuid
from .core import SafetyError
from .native_once import planning_access
from .native_reminder_groups import inventory as root_inventory, valid_id
from .native_reminder_moves import list_scope
from .native_reminder_smart_lists import snapshot as read_view
from .native_reminder_smart_sources import ids, encoded, normalize_date_filter

COVERAGE = 'bounded_custom_smart_list_inventory_root_order_and_referenced_private_lists'
RECOVERY_EFFECT = ('Only the complete bounded custom-view inventory, private root-group inventory, account order and referenced list scopes are compared. '
                   'The preassigned ID is absent from that observed custom-view inventory, not proven absent globally. '
                   'Creation success, causal dispatch, delayed effects and unexposed metadata remain unproven. No replay.')


def inventory(adapter, source):
    # One complete page avoids combining a moving multi-page collection.
    from .native_read import validate_smart_list_page
    value = adapter.reader.read('smart_lists', source_id=source, limit=100, offset=0)
    validate_smart_list_page(value, source, 100, 0)
    rows = value['smart_lists']
    if (value['done'] is not True or value['next_offset'] != len(rows)
            or any(not valid_id(row['id']) or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in row['title'])
                   or len(row['title'].encode()) > 4000 for row in rows)
            or rows != sorted(rows, key=lambda row: row['id'])):
        raise SafetyError('Creation requires a complete bounded custom Smart List inventory')
    return rows


def snapshot(adapter, item):
    source = item['source_id']
    roots = root_inventory(adapter, source)
    views = inventory(adapter, source)
    scopes = [list_scope(adapter, source, identity) for identity in ids(item['fields']['list_ids'])]
    if root_inventory(adapter, source) != roots or inventory(adapter, source) != views:
        raise SafetyError('Root order or custom Smart List inventory changed during review')
    return {'roots': roots, 'views': views, 'scopes': scopes}


def prepare(adapter, change):
    if (not isinstance(change, dict) or set(change) != {'operation', 'source_id', 'fields'}
            or change.get('operation') != 'create_smart_list'
            or change['source_id'] not in planning_access('reminders')['source_ids']
            or not isinstance(change['fields'], dict) or set(change['fields']) not in ({'title','list_ids'},{'title','list_ids','date_filter'})):
        raise SafetyError('Create a custom Smart List using exact source_id, fields.title and fields.list_ids')
    title = change['fields']['title']
    if (not isinstance(title, str) or not title.strip() or len(title) > 1000
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in title) or len(title.encode()) > 4000):
        raise SafetyError('Use a bounded nonempty Smart List title without control characters')
    item = {'operation': 'create_smart_list', 'source_id': change['source_id'], 'id': str(uuid.uuid4()).upper(),
            'fields': {'title': title, 'list_ids': ids(change['fields']['list_ids'])}}
    if 'date_filter' in change['fields']:item['fields']['date_filter']=normalize_date_filter(change['fields']['date_filter'])
    item['before'] = snapshot(adapter, item)
    before = item['before']
    if (len(before['views']) >= 100 or len(before['roots']['account_order']) >= 1000
            or any(row['title'] == title or row['id'] == item['id'] for row in before['views'])
            or item['id'] in before['roots']['account_order']):
        raise SafetyError('Duplicate custom-view title or identity, or bounded native inventory is full')
    item.update(fidelity_verified=True, effects={
        'view': 'Create one custom Smart List with a server-preassigned ID and a positive Lists-AND filter over the exact approved physical lists, optionally with the explicitly reviewed Today date criterion.',
        'scope': 'One to ten selected-source private ordinary nongrocery writable physical lists. No excluded lists, unknown criteria or sharing. The optional Today criterion follows the displaying device current day; other-device semantics are unverified.',
        'ordering': 'Append the new view ID to native root account order, preserving every existing entry. Apple controls app sorting and synchronization.',
        'preservation': 'No existing Smart List, Reminder, physical list, group, section or template is edited or moved. The view displays existing Reminders without copying them.',
        'date_filter':item['fields'].get('date_filter'),
        'cleanup': 'No Smart List deletion operation is included. This creates a persistent native view.', 'quota_objects': 1})
    return item


def expected_snapshot(item):
    value = deepcopy(item['before'])
    value['roots']['account_order'].append(item['id'])
    value['views'] = sorted(value['views'] + [{'id': item['id'], 'title': item['fields']['title']}], key=lambda row: row['id'])
    return value


def record_matches(item, observed):
    try:
        view = {'protocol_version': 1, 'source_id': item['source_id'], 'id': item['id'], 'title': item['fields']['title'],
                'minimum_supported_version': 20220430, 'definition_json': encoded(item['fields']['list_ids'],date_filter=item['fields'].get('date_filter')), 'data_mutated': False, 'backend': 'reminderkit-smart-definition-v2'}
        return (item['operation'] == 'create_smart_list' and observed.get('state') == 'custom_smart_list_created'
                and observed.get('id') == item['id'] and observed.get('source_id') == item['source_id']
                and observed.get('snapshot') == expected_snapshot(item) and observed.get('view') == view)
    except (KeyError, TypeError, AttributeError, ValueError, SafetyError):
        return False


def expected_observation(adapter, item):
    value = {'state': 'custom_smart_list_created', 'id': item['id'], 'source_id': item['source_id'],
             'snapshot': snapshot(adapter, item), 'view': read_view(item['source_id'], item['id'], reader=adapter.reader)}
    if not record_matches(item, value):
        raise SafetyError('Exact created custom view, source filter and preserved root inventory do not verify')
    return value


def verify(adapter, item, outcome):
    if any(outcome.get(key) != item[key] for key in ('id', 'source_id')):
        return False
    expected_observation(adapter, item)
    return True
