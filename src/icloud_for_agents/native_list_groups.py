"""Explicit physical-list grouping and root restoration with exact native anchors."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_reminder_moves import list_scope
from .native_reminder_groups import inventory,valid_id


def root_anchor(adapter,source,identity,groups):
    if any(g['id']==identity for g in groups['groups']):return {'id':identity,'source_id':source,'role':'private_root_group'}
    scope=list_scope(adapter,source,identity);parent=adapter.reader.read('list_parent',list_id=identity)
    if parent.get('parent') is not None:raise SafetyError('Ungrouping requires an exact root list or root group anchor')
    return {'id':identity,'source_id':source,'role':'private_root_list','scope':scope,'parent':parent}


def snapshot(adapter,item):
    source=item['source_id'];identity=item['list_id'];scope=list_scope(adapter,source,identity);groups=inventory(adapter,source);parent=adapter.reader.read('list_parent',list_id=identity);membership=adapter.reader.read('list_membership',list_id=identity)
    if (parent.get('source_id')!=source or parent.get('list_id')!=identity or membership.get('source_id')!=source or membership.get('list_id')!=identity):raise SafetyError('Exact list source, parent and task membership are required')
    if (type(membership.get('count')) is not int or not 0<=membership['count']<=100000 or not isinstance(membership.get('sha256'),str) or len(membership['sha256'])!=64 or any(c not in '0123456789abcdef' for c in membership['sha256'])):raise SafetyError('Complete bounded task-ID membership is required')
    membership={key:membership[key] for key in ('source_id','list_id','count','sha256')}
    group=item['fields']['group_id'] if item['operation']=='group_list' else item['origin_group_id']
    if not any(g['id']==group for g in groups['groups']):raise SafetyError('Select a verified private root group in this source')
    children=adapter.reader.read('group_lists',source_id=source,group_id=group,limit=100,offset=0,include_smart_lists=True)
    if (children.get('backend')!='reminderkit-group-lists-v3' or children.get('custom_smart_lists_included') is not True or children.get('source_id')!=source
            or children.get('group_id')!=group or children.get('done') is not True or children.get('data_mutated') is not False or not isinstance(children.get('lists'),list) or len(children['lists'])>100):raise SafetyError('A finished bounded physical and custom Smart List group view is required')
    anchor=root_anchor(adapter,source,item['fields']['anchor_id'],groups) if item['operation']=='ungroup_list' else None
    return {'source_scope':scope,'membership':membership,'inventory':groups,'parent':parent,'group_members':children,'anchor':anchor}


def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','list_id','fields'} or change.get('operation') not in {'group_list','ungroup_list'}
            or change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['fields'],dict)):
        raise SafetyError('Use exact source/list IDs and explicit group_list or ungroup_list fields')
    fields=change['fields'];grouping=change['operation']=='group_list'
    try:
        if not valid_id(change['list_id']) or set(fields)!=({'group_id'} if grouping else {'anchor_id','placement'}):raise ValueError()
        if not valid_id(fields['group_id'] if grouping else fields['anchor_id']) or (not grouping and fields['placement'] not in ('before','after')):raise ValueError()
        if change['list_id']==(fields['group_id'] if grouping else fields['anchor_id']):raise ValueError()
    except (ValueError,TypeError,AttributeError):raise SafetyError('Select distinct exact UUIDs and before/after for root restoration') from None
    item=deepcopy(change);item['id']=item['list_id']
    if not grouping:
        parent=adapter.reader.read('list_parent',list_id=item['list_id']).get('parent')
        if not isinstance(parent,dict):raise SafetyError('The exact list is already outside an observed group')
        item['origin_group_id']=parent['id']
    item['before']=snapshot(adapter,item);before=item['before'];parent=before['parent'].get('parent')
    if grouping:
        if parent is not None or before['group_members']['lists']!=[]:raise SafetyError('Grouping currently requires a root physical list and an observed empty destination group')
    else:
        rows=[v for v in before['group_members']['lists'] if v['id']==item['id']]
        if parent is None or parent.get('id')!=item['origin_group_id'] or len(rows)!=1 or rows[0].get('kind')!='list' or rows[0].get('is_group') is not False:raise SafetyError('Exact parent and provider-reported physical group membership must agree')
    order=before['inventory']['account_order']
    if order.count(item['id'])!=1 or (not grouping and order.count(fields['anchor_id'])!=1):raise SafetyError('The selected list must appear exactly once in native account order')
    item.update(fidelity_verified=True,effects={'list':'Group only this existing private ordinary physical list, or return it to the source root at the exact anchor. No cross-account movement or group creation.',
        'scope':'Grouping requires an empty observed physical/custom Smart List destination; ungrouping removes only this exact physical child. Nested, shared, grocery and Smart List moves are excluded.',
        'tasks':'No reminder, section, template or Smart List is written. Complete bounded task-ID membership is checked; individual task content and unexposed representations are not re-exported.',
        'order':'Only the selected list entry changes position in native account order. Other entries retain relative order; app display sorting and cloud synchronization can differ.',
        'task_count':before['membership']['count'],'quota_objects':1})
    return item


def expected_snapshot(item):
    result=deepcopy(item['before']);identity=item['id'];fields=item['fields'];grouping=item['operation']=='group_list';order=[value for value in result['inventory']['account_order'] if value!=identity]
    if grouping:
        order.insert(0,identity);group=next(g for g in result['inventory']['groups'] if g['id']==fields['group_id']);result['parent']['parent']={'id':group['id'],'title':group['title']};result['parent']['parent_status']='provider_reported'
        result['group_members']['lists']=[{'id':identity,'title':result['source_scope']['list']['title'],'is_group':False,'kind':'list'}]
    else:
        anchor=fields['anchor_id'];index=order.index(anchor)+(fields['placement']=='after');order.insert(index,identity);result['parent']['parent']=None;result['parent']['parent_status']='provider_not_reported';result['group_members']['lists']=[v for v in result['group_members']['lists'] if v['id']!=identity]
    result['inventory']['account_order']=order;result['group_members']['next_offset']=len(result['group_members']['lists']);return result


def record_matches(item,observed):
    try:return (observed.get('state')==('list_grouped' if item['operation']=='group_list' else 'list_ungrouped') and observed.get('id')==item['id'] and observed.get('source_id')==item['source_id'] and observed.get('snapshot')==expected_snapshot(item))
    except (KeyError,TypeError,AttributeError,ValueError):return False


def expected_observation(adapter,item):
    value={'state':'list_grouped' if item['operation']=='group_list' else 'list_ungrouped','id':item['id'],'source_id':item['source_id'],'snapshot':snapshot(adapter,item)}
    if not record_matches(item,value):raise SafetyError('Exact list parent, task membership, group view and account order do not verify')
    return value


def verify(adapter,item,outcome):
    if any(outcome.get(key)!=item[key] for key in ('id','source_id','list_id')):return False
    expected_observation(adapter,item);return True
