"""Explicit native list badge changes with exact membership and role snapshots."""
from .core import SafetyError
from .native_once import planning_access
from .native_read import native_config
from .native_reminder_moves import list_scope


def valid_badge(value):
    import re
    if not isinstance(value,dict):return False
    if value=={'kind':'default'}:return True
    if set(value)!={'kind','value'} or not isinstance(value['value'],str):return False
    text=value['value']
    if value['kind']=='symbol':return bool(re.fullmatch(r'[a-z][a-z0-9.]{0,99}',text))
    return value['kind']=='emoji' and 1<=len(text)<=32 and any(ord(c)>0x2000 for c in text) and all(ord(c)>=32 for c in text)


def snapshot(adapter,item):
    scope=list_scope(adapter,item['source_id'],item['list_id'])
    base=adapter.list_rename_snapshot(item)
    if base['list']!=scope['list']:raise SafetyError('List changed during badge review')
    features=adapter.reader.read('list_features',list_id=item['list_id'],include_badge=True)
    if (features.get('source_id')!=item['source_id'] or features.get('list_id')!=item['list_id']
            or not valid_badge(features.get('badge')) or features.get('data_mutated') is not False
            or any(features.get(key)!=value for key,value in scope['role'].items())):
        raise SafetyError('Exact native list badge and ordinary private role are unavailable')
    return dict(base,list_features=features)


def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','list_id','fields'}
            or change.get('operation')!='set_list_badge' or change['source_id'] not in planning_access('reminders')['source_ids']
            or not isinstance(change['list_id'],str) or not 1<=len(change['list_id'])<=1000
            or native_config('reminders').get('advanced_backend')!='reminderkit'):
        raise SafetyError('Select one exact native Reminder list and the ReminderKit backend')
    fields=change['fields']
    if not isinstance(fields,dict) or set(fields)!={'badge'} or not valid_badge(fields['badge']):
        raise SafetyError('List badge changes requires exactly one explicit badge object: kind default, or kind emoji/symbol with value')
    item=dict(change,id=change['list_id'],fields=dict(fields),fidelity_verified=True,
              effects={'list':'Change only this ordinary private list badge; preserve title, color and complete reminder membership.',
                       'validation':'A single native emoji or an available system symbol is checked before saving. Cross-device synchronization is unconfirmed.'})
    item['before']=snapshot(adapter,item)
    if item['before']['list_features']['badge']==fields['badge']:raise SafetyError('List already has the requested badge state')
    return item


def verify(adapter,item,outcome):
    if (outcome.get('id')!=item['list_id'] or outcome.get('source_id')!=item['source_id']
            or outcome.get('badge')!=item['fields']['badge']):return False
    actual=snapshot(adapter,item);expected=dict(item['before'],list_features=dict(item['before']['list_features'],badge=item['fields']['badge']))
    return actual==expected
