"""Assign one private grocery root leaf to an exact existing custom category."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_reminder_moves import check_item_graph
from .native_grocery_categories import private_scope as list_scope
from .native_reminder_section_store import read as read_store,identity

COVERAGE='exact_private_grocery_list_category_inventory_membership_map_manual_order_and_all_bounded_root_reminders'
RECOVERY_EFFECT='Only the reviewed list, sections, local membership map, manual order and exposed Reminder snapshots are compared. Causal dispatch, delayed effects, synchronization and unexposed metadata remain unproven. No replay.'


def sections(adapter,source,list_id):
    value=adapter.reader.read('list_sections',list_id=list_id)
    if (value.get('source_id')!=source or value.get('list_id')!=list_id or value.get('data_mutated') is not False
            or not isinstance(value.get('sections'),list) or len(value['sections'])>100):raise SafetyError('Exact bounded same-list section inventory required')
    return value


def snapshot(adapter,item):
    source=item['source_id'];list_id=item['list_id'];scope=list_scope(adapter,source,list_id)
    graph=adapter.reader.read('list_order',list_id=list_id);inventory=sections(adapter,source,list_id)
    if graph.get('source_id')!=source or graph.get('list_id')!=list_id or not 1<=len(graph['nodes'])<=20:raise SafetyError('Grocery category changes require a complete 1..20-item list graph and native manual order')
    items=[adapter.snapshot({'operation':'update_reminder','source_id':source,'id':row['id'],'fields':{}}) for row in graph['nodes']]
    for value in items:
        admission=deepcopy(value);admission['native_metadata'].update(section=None,section_status='provider_not_reported');check_item_graph(admission,graph,value['item']['id'])
        if value['item'].get('completed') is not False or value['native_metadata'].get('parent_id') is not None or value['native_metadata'].get('child_ids')!=[]:raise SafetyError('Grocery assignment requires incomplete nonrecurring root leaves throughout this bounded list')
    selected=next((v for v in items if v['item']['id']==item['id']),None)
    if selected is None or selected['item'].get('completed') is not False:raise SafetyError('Select an incomplete root leaf in this exact list')
    unsectioned=deepcopy(selected);unsectioned['native_metadata'].update(section=None,section_status='provider_not_reported');check_item_graph(unsectioned,graph,item['id'])
    if (selected['native_metadata'].get('parent_id') is not None or selected['native_metadata'].get('is_subtask') is not False
            or selected['native_metadata'].get('child_ids')!=[] or any(v['parent_id']==item['id'] for v in graph['nodes'])):raise SafetyError('Grocery category changes currently support existing root leaves without descendants')
    destination=item['fields']['category_id'];by_id={v['id']:v for v in inventory['sections']}
    if destination is not None and destination not in by_id:raise SafetyError('Select an exact existing custom grocery category of this same private list')
    stored=read_store(source,list_id);rows={v['memberID']:v for v in stored['value']['memberships']};observed={v['item']['id']:v for v in items}
    for member,row in rows.items():
        if 'groupID' in row and (member not in observed or row['groupID'] not in by_id):raise SafetyError('Positive section membership references an unobserved member or section')
    for member,value in observed.items():
        native=value['native_metadata'];group=rows.get(member,{}).get('groupID');expected=None if group is None else by_id[group]
        if (value['item'].get('list_id')!=list_id or value['item'].get('source_id')!=source
                or native.get('section')!=expected or native.get('section_status')!=('provider_not_reported' if expected is None else 'provider_reported')):
            raise SafetyError('Native section observations and fixed local membership map disagree')
    if adapter.reader.read('list_order',list_id=list_id)!=graph or sections(adapter,source,list_id)!=inventory:raise SafetyError('List hierarchy, order or sections changed during review')
    return {'source_scope':scope,'source_graph':graph,'sections':inventory,'items':items,'membership_store':stored}


def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','id','fields'} or change.get('operation')!='set_grocery_category'
            or change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['fields'],dict) or set(change['fields'])!={'category_id'}):raise SafetyError('Select an exact reminder/source and explicit category_id, or null to remove section membership')
    identity(change['source_id']);identity(change['id'])
    if change['fields']['category_id'] is not None:identity(change['fields']['category_id'])
    current=adapter.reader.read('get',id=change['id'])['item']
    if current.get('id')!=change['id'] or current.get('source_id')!=change['source_id']:raise SafetyError('Reminder is outside the selected source')
    item=deepcopy(change);item['list_id']=current['list_id'];item['before']=snapshot(adapter,item)
    selected=next(v for v in item['before']['items'] if v['item']['id']==item['id']);section=selected['native_metadata']['section']
    if (section['id'] if section else None)==change['fields']['category_id']:raise SafetyError('Reminder already has the requested section membership')
    item.update(fidelity_verified=True,effects={
        'change':'Change only the grocery category membership of this existing incomplete nonrecurring root leaf; explicit null removes it from its section.',
        'scope':'One selected private grocery list without shopping-session state with at most 20 live items. Same-list existing sections only; no hierarchy, section creation, deletion or cross-list move.',
        'preservation':'Verify the complete bounded list graph, native manual order, section inventory, local membership map and all exposed Reminder snapshots. No content, date, alarm, completion or flag change is requested.',
        'metadata':'Requires the separately opted-in read-only Reminders store guard. Only the fixed native membership setter writes; SQLite is read-only.',
        'limits':'Apple controls display sorting and synchronization. Unexposed fields and simultaneous provider changes are not certified atomic.',
        'quota_objects':1})
    return item


def record_matches(item,observed):
    try:
        before=item['before'];after=observed['snapshot'];destination=item['fields']['category_id']
        if (item['operation']!='set_grocery_category' or observed.get('state')!='reminder_grocery_category_changed' or observed.get('id')!=item['id'] or observed.get('source_id')!=item['source_id']
                or any(after.get(k)!=before[k] for k in ('source_scope','source_graph','sections')) or len(after['items'])!=len(before['items'])):return False
        section=next((v for v in before['sections']['sections'] if v['id']==destination),None)
        if destination is not None and section is None:return False
        for old,new in zip(before['items'],after['items']):
            expected=deepcopy(old)
            if old['item']['id']==item['id']:expected['native_metadata'].update(section=section,section_status='provider_not_reported' if section is None else 'provider_reported')
            # Membership is list metadata; individual Reminder content/modified
            # timestamps must remain exact, including every bystander.
            if new!=expected:return False
        old_store=before['membership_store'];new_store=after['membership_store']
        if new_store['schema_sha256']!=old_store['schema_sha256'] or new_store['list_revision']<old_store['list_revision'] or new_store['value']['minimumSupportedVersion']!=old_store['value']['minimumSupportedVersion']:return False
        old={v['memberID']:v for v in old_store['value']['memberships']};new={v['memberID']:v for v in new_store['value']['memberships']}
        prior=old.pop(item['id'],None);changed=new.pop(item['id'],None)
        if old!=new or changed is None or changed.get('groupID')!=destination or changed['modifiedOn']<=(prior or {}).get('modifiedOn',0):return False
        return set(changed)==({'memberID','modifiedOn'} if destination is None else {'memberID','groupID','modifiedOn'})
    except (KeyError,TypeError,AttributeError,ValueError):return False


def expected_observation(adapter,item):
    value={'state':'reminder_grocery_category_changed','id':item['id'],'source_id':item['source_id'],'snapshot':snapshot(adapter,item)}
    if not record_matches(item,value):raise SafetyError('Exact section membership and preserved list contents/order do not verify')
    return value


def verify(adapter,item,outcome):
    if any(outcome.get(k)!=item[k] for k in ('id','source_id','list_id')):return False
    expected_observation(adapter,item);return True
