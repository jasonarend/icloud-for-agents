"""Relative root-group order with exact account sequence and mixed child view."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_reminder_groups import inventory,valid_id
COVERAGE='reviewed_root_group_mixed_children_anchor_and_complete_account_sequence'
RECOVERY_EFFECT='Only the reviewed root group, bounded mixed child view, anchor and account sequence are compared. Causal dispatch, delayed effects and unexposed metadata remain unproven. No replay.'

def snapshot(adapter,item):
    from .native_list_groups import root_anchor
    source=item['source_id'];groups=inventory(adapter,source)
    if not any(g['id']==item['id'] for g in groups['groups']):raise SafetyError('Select an exact private root group')
    anchor=root_anchor(adapter,source,item['fields']['anchor_id'],groups);children=adapter.reader.read('group_lists',source_id=source,group_id=item['id'],limit=100,offset=0,include_smart_lists=True)
    if (children.get('backend')!='reminderkit-group-lists-v3' or children.get('custom_smart_lists_included') is not True or children.get('source_id')!=source
            or children.get('group_id')!=item['id'] or children.get('done') is not True or children.get('data_mutated') is not False or not isinstance(children.get('lists'),list) or len(children['lists'])>100):raise SafetyError('A finished bounded physical and custom Smart List group view is required')
    if item['fields']['anchor_id'] not in groups['account_order']:raise SafetyError('The exact root anchor is absent from native account order')
    return {'inventory':groups,'children':children,'anchor':anchor}

def expected_snapshot(item):
    result=deepcopy(item['before']);order=result['inventory']['account_order'];order.remove(item['id']);index=order.index(item['fields']['anchor_id'])+(item['fields']['placement']=='after');order.insert(index,item['id']);return result

def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','group_id','fields'} or change.get('operation')!='reorder_group'
            or change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['fields'],dict) or set(change['fields'])!={'anchor_id','placement'}):raise SafetyError('Reorder one exact private root group with an exact root anchor and before/after placement')
    try:
        if not valid_id(change['group_id']) or not valid_id(change['fields']['anchor_id']) or change['group_id']==change['fields']['anchor_id'] or change['fields']['placement'] not in ('before','after'):raise ValueError()
    except (ValueError,TypeError,AttributeError):raise SafetyError('Use distinct exact UUIDs and before/after placement') from None
    item={'operation':'reorder_group','source_id':change['source_id'],'id':change['group_id'],'fields':deepcopy(change['fields'])};item['before']=snapshot(adapter,item)
    if expected_snapshot(item)==item['before']:raise SafetyError('Group already occupies this native relative position')
    item.update(fidelity_verified=True,effects={'order':'Move only this root group entry beside the exact root list/group anchor in native account order. Other entries retain relative order; app sorting and cloud sync can differ.',
        'content':'Keep group identity, title and observed physical/custom Smart List children. No child, reminder, section, template or Smart List is written.',
        'coverage':'At most100 observed children and1000 account entries. Complete unexposed hierarchy and cross-device behavior are not established.','quota_objects':1})
    return item

def record_matches(item,observed):
    try:return (observed.get('state')=='group_reordered' and observed.get('id')==item['id'] and observed.get('source_id')==item['source_id'] and observed.get('snapshot')==expected_snapshot(item))
    except (KeyError,ValueError,TypeError,AttributeError):return False

def expected_observation(adapter,item):
    value={'state':'group_reordered','id':item['id'],'source_id':item['source_id'],'snapshot':snapshot(adapter,item)}
    if not record_matches(item,value):raise SafetyError('Exact group, children, anchor and native account sequence do not verify')
    return value

def verify(adapter,item,outcome):
    if any(outcome.get(key)!=item[key] for key in ('id','source_id')):return False
    expected_observation(adapter,item);return True
