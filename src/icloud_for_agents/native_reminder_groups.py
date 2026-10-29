"""Bounded native root-group inventory and explicit identity validation."""
import uuid
from .core import SafetyError


def valid_id(value):
    return isinstance(value,str) and str(uuid.UUID(value)).upper()==value


def validate_inventory(value,source):
    try:
        if (not isinstance(value,dict) or set(value)!={'protocol_version','source_id','groups','account_order','data_mutated','backend'}
                or value['protocol_version']!=1 or value['source_id']!=source or value['data_mutated'] is not False or value['backend']!='reminderkit-root-group-inventory-v1'
                or not isinstance(value['groups'],list) or len(value['groups'])>100 or not isinstance(value['account_order'],list) or len(value['account_order'])>1000):raise ValueError()
        order=value['account_order'];seen=set()
        if any(not valid_id(identity) for identity in order) or len(order)!=len(set(order)):raise ValueError()
        for group in value['groups']:
            if (not isinstance(group,dict) or set(group)!={'id','title'} or not valid_id(group['id']) or group['id'] in seen or group['id'] not in order
                    or not isinstance(group['title'],str) or not 1<=len(group['title'])<=1000 or len(group['title'].encode())>4000):raise ValueError()
            seen.add(group['id'])
        if value['groups']!=sorted(value['groups'],key=lambda row:row['id']):raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError,UnicodeError):raise SafetyError('Exact private root groups and account order could not be verified') from None
    return value


def inventory(adapter,source):
    from .native_read import native_config
    config=native_config('reminders')
    if source not in config.get('source_ids',[]) or config.get('advanced_backend')!='reminderkit':raise SafetyError('Group operations require an exact selected source and ReminderKit backend')
    return validate_inventory(adapter.reader.read('group_inventory',source_id=source),source)


def prepare_create(adapter,change):
    from .native_once import planning_access
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','fields'} or change.get('operation')!='create_group'
            or change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['fields'],dict) or set(change['fields'])!={'title'}):raise SafetyError('Create a root group using exact source_id and fields.title')
    title=change['fields']['title']
    if not isinstance(title,str) or not title.strip() or len(title)>1000 or len(title.encode())>4000 or any(ord(c)<32 for c in title):raise SafetyError('Use a nonempty bounded group title without control characters')
    before=inventory(adapter,change['source_id'])
    if len(before['groups'])>=100 or len(before['account_order'])>=1000 or any(row['title']==title for row in before['groups']):raise SafetyError('Duplicate group title or bounded native inventory is full')
    return {'operation':'create_group','source_id':change['source_id'],'id':str(uuid.uuid4()).upper(),'fields':{'title':title},'before':before,'fidelity_verified':True,
            'effects':{'group':'Create one empty private root list group with a preassigned native ID in this exact source.',
                       'scope':'Append only the new group ID to account order. No reminders, lists, smart lists, sections or templates are moved, copied, removed or shared.',
                       'ordering':'Existing native account-order entries retain their order; app display sorting and cloud synchronization can differ.'}}


def expected_inventory(item):
    from copy import deepcopy
    value=deepcopy(item['before']);value['groups']=sorted(value['groups']+[{'id':item['id'],'title':item['fields']['title']}],key=lambda row:row['id']);value['account_order'].append(item['id']);return value


def expected_observation(adapter,item):
    current=inventory(adapter,item['source_id']);children=adapter.reader.read('group_lists',source_id=item['source_id'],group_id=item['id'],limit=100,offset=0)
    value={'state':'group_created','id':item['id'],'source_id':item['source_id'],'inventory':current,'children':children}
    if not record_matches(item,value):raise SafetyError('New empty root group and preserved account inventory do not verify')
    return value


def record_matches(item,observed):
    try:
        children=observed['children']
        return (observed.get('state')=='group_created' and observed.get('id')==item['id'] and observed.get('source_id')==item['source_id']
                and observed.get('inventory')==expected_inventory(item) and children.get('source_id')==item['source_id'] and children.get('group_id')==item['id']
                and children.get('lists')==[] and children.get('done') is True and children.get('next_offset')==0 and children.get('data_mutated') is False
                and children.get('backend')=='reminderkit-group-lists-v2')
    except (KeyError,TypeError,AttributeError):return False


def verify_create(adapter,item,outcome):
    if outcome.get('id')!=item['id'] or outcome.get('source_id')!=item['source_id']:return False
    expected_observation(adapter,item);return True
