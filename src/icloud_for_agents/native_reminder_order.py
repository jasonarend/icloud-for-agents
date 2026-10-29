"""Exact root ordering relative to a reviewed root anchor; preserve hierarchy."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_reminder_moves import list_scope,subtree,check_item_graph,charge,PRESERVED_NATIVE


def reordered(order,identity,anchor,placement):
    if identity==anchor or placement not in {'before','after'} or order.count(identity)!=1 or order.count(anchor)!=1:raise SafetyError('Select distinct exact roots and placement before or after')
    result=[v for v in order if v!=identity];index=result.index(anchor)+(placement=='after');result.insert(index,identity);return result


def snapshot(adapter,item):
    source=item['source_id'];scope=list_scope(adapter,source,item['list_id']);graph=adapter.reader.read('list_order',list_id=item['list_id'])
    if graph.get('source_id')!=source or graph.get('list_id')!=item['list_id']:raise SafetyError('Manual order belongs to another source or list')
    identities=subtree(graph,item['id']);anchor=item['fields']['anchor_id'];subtree(graph,anchor)
    snapshots=[]
    for identity in identities+[anchor]:
        value=adapter.snapshot({'operation':'update_reminder','source_id':source,'id':identity,'fields':{}});check_item_graph(value,graph,identity);snapshots.append(value)
    before=dict(snapshots[0]);before.update(source_scope=scope,source_graph=graph,descendants=snapshots[1:-1],anchor=snapshots[-1]);return before


def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','id','fields'} or change.get('operation')!='reorder_reminder'
            or change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['fields'],dict)
            or set(change['fields'])!={'anchor_id','placement'} or not isinstance(change['fields']['placement'],str) or change['fields']['placement'] not in {'before','after'}):raise SafetyError('Ordering requires exact source/id and fields.anchor_id plus placement before or after')
    if any(not isinstance(value,str) or not 1<=len(value)<=1000 for value in (change['id'],change['fields']['anchor_id'])):raise SafetyError('Select exact saved root and anchor identities')
    current=adapter.reader.read('get',id=change['id'])['item']
    if current.get('id')!=change['id'] or current.get('source_id')!=change['source_id']:raise SafetyError('Reminder is outside the selected source')
    item=deepcopy(change);item['list_id']=current['list_id'];item['before']=snapshot(adapter,item);before=item['before'];order=before['source_graph']['ordered_ids']
    if reordered(order,item['id'],item['fields']['anchor_id'],item['fields']['placement'])==order:raise SafetyError('Root is already at the requested relative position')
    item.update(fidelity_verified=True,effects={'order':'Change only this root entry in the native manual sequence relative to the exact root anchor. Other sequence entries retain relative order; native app display sorting can differ.',
        'hierarchy':'Preserve all parent/child relationships and reminder content. Subtasks, recurring and sectioned roots/anchors are refused; no reparenting.',
        'affected_objects':[{'id':v['item']['id'],'title':v['item']['title']} for v in [before,*before['descendants']]],'quota_objects':charge(item),
        'notifications':'No dates, alarms or completion changes. Apple controls synchronization.'})
    return item


def verify(adapter,item,outcome):
    before=item['before'];source=item['source_id']
    if any(outcome.get(key)!=item[key] for key in ('id','source_id','list_id')):return False
    expected=deepcopy(before['source_graph']);expected['ordered_ids']=reordered(expected['ordered_ids'],item['id'],item['fields']['anchor_id'],item['fields']['placement'])
    if list_scope(adapter,source,item['list_id'])!=before['source_scope'] or adapter.reader.read('list_order',list_id=item['list_id'])!=expected:return False
    for previous in [before,*before['descendants'],before['anchor']]:
        observed=adapter.snapshot({'operation':'update_reminder','source_id':source,'id':previous['item']['id'],'fields':{}});check_item_graph(observed,expected,previous['item']['id'])
        if {k:v for k,v in observed['item'].items() if k!='modified'}!={k:v for k,v in previous['item'].items() if k!='modified'}:return False
        if observed['native_metadata']!=previous['native_metadata'] or observed['urgent'] is not previous['urgent']:return False
    return True


def unchanged(adapter,item,root):
    current=snapshot(adapter,item);return current==item['before']


def expected_observation(adapter,item):
    observed={'state':'reordered','id':item['id'],'source_id':item['source_id'],'snapshot':snapshot(adapter,item)}
    if not record_matches(item,observed):raise SafetyError('Expected root order and preserved state do not verify')
    return observed


def record_matches(item,observed):
    try:
        if (observed.get('state')!='reordered' or observed.get('id')!=item['id'] or observed.get('source_id')!=item['source_id']):return False
        current=observed['snapshot'];before=item['before'];expected=deepcopy(before['source_graph']);expected['ordered_ids']=reordered(expected['ordered_ids'],item['id'],item['fields']['anchor_id'],item['fields']['placement'])
        if current.get('source_scope')!=before['source_scope'] or current.get('source_graph')!=expected:return False
        old=[before,*before['descendants'],before['anchor']];new=[current,*current['descendants'],current['anchor']]
        if len(old)!=len(new):return False
        for previous,actual in zip(old,new):
            if actual.get('list')!=previous['list'] or actual.get('native_metadata')!=previous['native_metadata'] or actual.get('urgent') is not previous['urgent']:return False
            if {k:v for k,v in actual['item'].items() if k!='modified'}!={k:v for k,v in previous['item'].items() if k!='modified'}:return False
        return True
    except (KeyError,TypeError,AttributeError,ValueError,SafetyError):return False
