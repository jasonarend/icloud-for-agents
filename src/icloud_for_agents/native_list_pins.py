"""Explicit native list pin changes with exact membership and role snapshots."""
from .core import SafetyError
from .native_once import planning_access
from .native_read import native_config
from .native_reminder_moves import list_scope


def snapshot(adapter,item):
    scope=list_scope(adapter,item['source_id'],item['list_id'])
    base=adapter.list_rename_snapshot(item)
    if base['list']!=scope['list']:raise SafetyError('List changed during pin review')
    features=adapter.reader.read('list_features',list_id=item['list_id'],include_pinned=True)
    if (features.get('source_id')!=item['source_id'] or features.get('list_id')!=item['list_id']
            or type(features.get('pinned')) is not bool or features.get('data_mutated') is not False
            or any(features.get(key)!=value for key,value in scope['role'].items())):
        raise SafetyError('Exact native list pin and ordinary private role are unavailable')
    return dict(base,list_features=features)


def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','list_id','fields'}
            or change.get('operation')!='set_list_pinned' or change['source_id'] not in planning_access('reminders')['source_ids']
            or not isinstance(change['list_id'],str) or not 1<=len(change['list_id'])<=1000
            or native_config('reminders').get('advanced_backend')!='reminderkit'):
        raise SafetyError('Select one exact native Reminder list and the ReminderKit backend')
    fields=change['fields']
    if not isinstance(fields,dict) or set(fields)!={'pinned'} or type(fields['pinned']) is not bool:
        raise SafetyError('List pinning requires exactly one explicit pinned Boolean')
    item=dict(change,id=change['list_id'],fields=dict(fields),fidelity_verified=True,
              effects={'list':'Change only this ordinary private list pin; preserve title, color and complete reminder membership.',
                       'order':'Apple determines the pinned display position. Cross-device synchronization is unconfirmed.'})
    item['before']=snapshot(adapter,item)
    if item['before']['list_features']['pinned'] is fields['pinned']:raise SafetyError('List already has the requested pin state')
    return item


def verify(adapter,item,outcome):
    if (outcome.get('id')!=item['list_id'] or outcome.get('source_id')!=item['source_id']
            or outcome.get('pinned') is not item['fields']['pinned']):return False
    actual=snapshot(adapter,item);expected=dict(item['before'],list_features=dict(item['before']['list_features'],pinned=item['fields']['pinned']))
    return actual==expected
