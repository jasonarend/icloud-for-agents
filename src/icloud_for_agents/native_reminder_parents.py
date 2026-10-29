"""Move one existing incomplete leaf between root-parent families in one list."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_reminder_moves import list_scope,check_item_graph
COVERAGE='reviewed_leaf_old_new_root_parents_anchor_and_complete_list_order'
RECOVERY_EFFECT='Only the reviewed leaf, root parents, anchor and bounded list graph/manual order are compared. Causal dispatch, delayed effects and unexposed metadata remain unproven. No replay.'
HIERARCHY_FIELDS={'parent_id','is_subtask','has_subtasks','child_ids','children_status','children_fetch_status'}

def reordered(order,identity,fields):
    result=[v for v in order if v!=identity];anchor=fields['anchor_id'];index=len(result) if anchor is None else result.index(anchor)+(fields['placement']=='after');result.insert(index,identity);return result

def snapshot(adapter,item):
    source=item['source_id'];scope=list_scope(adapter,source,item['list_id']);graph=adapter.reader.read('list_order',list_id=item['list_id'])
    if graph.get('source_id')!=source or graph.get('list_id')!=item['list_id']:raise SafetyError('The exact list graph and manual order are required')
    values=[]
    for identity in item['family_ids']:
        value=adapter.snapshot({'operation':'update_reminder','source_id':source,'id':identity,'fields':{}});check_item_graph(value,graph,identity)
        if value['item'].get('completed') is not False or not isinstance(value['native_metadata'].get('child_ids'),list):raise SafetyError('Reparenting requires incomplete family objects and bounded direct-child observations')
        values.append(value)
    return {'source_scope':scope,'source_graph':graph,'items':values}

def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','id','fields'} or change.get('operation')!='reparent_reminder'
            or change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['fields'],dict) or set(change['fields'])!={'parent_id','anchor_id','placement'}):raise SafetyError('Select an exact reminder/source and explicit parent_id, anchor_id and placement')
    fields=change['fields'];identity=change['id'];parent=fields['parent_id'];anchor=fields['anchor_id'];placement=fields['placement']
    if (any(not isinstance(v,str) or not 1<=len(v)<=1000 for v in [identity,*([parent] if parent is not None else []),*([anchor] if anchor is not None else [])])
            or identity in (parent,anchor) or (parent is not None and parent==anchor) or not isinstance(placement,str)
            or (anchor is None and placement!='end') or (anchor is not None and placement not in ('before','after'))):raise SafetyError('Use distinct exact identities; null anchor requires explicit end placement')
    current=adapter.reader.read('get',id=identity)['item']
    if current.get('id')!=identity or current.get('source_id')!=change['source_id']:raise SafetyError('Reminder is outside the selected source')
    graph=adapter.reader.read('list_order',list_id=current['list_id']);nodes={v['id']:v for v in graph['nodes']};node=nodes.get(identity)
    if node is None or any(v['parent_id']==identity for v in nodes.values()):raise SafetyError('Reparenting currently requires one existing leaf; no descendant moves')
    old=node['parent_id']
    if old==parent:raise SafetyError('This reminder already belongs to the requested parent/root')
    for target in (old,parent):
        if target is not None and (target not in nodes or nodes[target]['parent_id'] is not None):raise SafetyError('Old and new parents must be existing roots in the same private list')
    if anchor is not None and (anchor not in nodes or nodes[anchor]['parent_id']!=parent):raise SafetyError('The exact anchor must already belong to the requested root-parent family')
    item=deepcopy(change);item.update(list_id=current['list_id'],old_parent_id=old,family_ids=sorted({identity,*([old] if old else []),*([parent] if parent else []),*([anchor] if anchor else [])}));item['before']=snapshot(adapter,item)
    if item['before']['source_graph']!=graph:raise SafetyError('Reminder hierarchy changed while preparing the family snapshot')
    affected=sorted({identity,*([old] if old else []),*([parent] if parent else [])})
    item.update(fidelity_verified=True,effects={'hierarchy':'Change only this existing leaf parent. An explicit null parent promotes it to a root. Old/new parents must be incomplete, nonrecurring roots in this same private ordinary nongrocery list; descendants and sections are refused.',
        'order':'Move only the selected leaf entry before/after an exact sibling anchor, or to the explicit end of native manual order. Other sequence entries keep relative order. App sorting and synchronization can differ.',
        'content':'Preserve reviewed family content, dates, alarms, tags, flag, urgency and other native metadata. Family child IDs/presence and modification times can change. No implicit completion, recurrence, cross-list move or content rewrite.',
        'affected_objects':affected,'quota_objects':len(affected)})
    return item

def expected_graph(item):
    value=deepcopy(item['before']['source_graph']);value['ordered_ids']=reordered(value['ordered_ids'],item['id'],item['fields']);value['nodes']=[dict(v,parent_id=item['fields']['parent_id']) if v['id']==item['id'] else v for v in value['nodes']];return value

def hierarchy_matches(value,graph):
    native=value.get('native_metadata',{});identity=value.get('item',{}).get('id');node=next((v for v in graph['nodes'] if v['id']==identity),None)
    if node is None:return False
    children=sorted(v['id'] for v in graph['nodes'] if v['parent_id']==identity);reported=native.get('has_subtasks')
    return (native.get('parent_id')==node['parent_id'] and native.get('is_subtask') is (node['parent_id'] is not None) and isinstance(native.get('child_ids'),list) and sorted(native['child_ids'])==children
            and native.get('children_fetch_status')=='provider_reported' and native.get('children_status')==('unavailable' if reported is None else 'provider_reported')
            and (reported is None or type(reported) is bool and reported is bool(children)))

def record_matches(item,observed):
    try:
        if observed.get('state')!='reminder_reparented' or observed.get('id')!=item['id'] or observed.get('source_id')!=item['source_id']:return False
        after=observed['snapshot'];before=item['before'];graph=expected_graph(item)
        if after.get('source_scope')!=before['source_scope'] or after.get('source_graph')!=graph or len(after['items'])!=len(before['items']):return False
        for previous,current in zip(before['items'],after['items']):
            if (current.get('list')!=previous['list'] or current.get('urgent') is not previous['urgent'] or not hierarchy_matches(current,graph)
                    or {k:v for k,v in current['item'].items() if k!='modified'}!={k:v for k,v in previous['item'].items() if k!='modified'}
                    or {k:v for k,v in current['native_metadata'].items() if k not in HIERARCHY_FIELDS}!={k:v for k,v in previous['native_metadata'].items() if k not in HIERARCHY_FIELDS}):return False
        return True
    except (KeyError,TypeError,AttributeError,ValueError):return False

def expected_observation(adapter,item):
    observed={'state':'reminder_reparented','id':item['id'],'source_id':item['source_id'],'snapshot':snapshot(adapter,item)}
    if not record_matches(item,observed):raise SafetyError('Exact new parent/order and preserved family content do not verify')
    return observed

def verify(adapter,item,outcome):
    if any(outcome.get(k)!=item[k] for k in ('id','source_id','list_id')):return False
    expected_observation(adapter,item);return True

def charge(item):
    """Count the leaf and both affected parents from bound graph identities."""
    try:
        nodes={v['id']:v for v in item['before']['source_graph']['nodes']};identity=item['id'];old=nodes[identity]['parent_id'];parent=item['fields']['parent_id'];anchor=item['fields']['anchor_id']
        affected={identity,*([old] if old else []),*([parent] if parent else [])};family=sorted(affected|({anchor} if anchor else set()))
        if (old!=item['old_parent_id'] or old==parent or any(v['parent_id']==identity for v in nodes.values()) or any(target not in nodes or nodes[target]['parent_id'] is not None for target in (old,parent) if target is not None)
                or item['family_ids']!=family or [v['item']['id'] for v in item['before']['items']]!=family):raise ValueError()
        return len(affected)
    except (KeyError,ValueError,TypeError,AttributeError):raise SafetyError('Reparent quota requires the exact reviewed leaf and root-parent family') from None
