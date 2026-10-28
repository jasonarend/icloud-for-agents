"""Exact root-subtree moves using bounded native/EventKit list graphs."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_read import native_config

ROLE_FLAGS=('grocery_categorization','grocery_shopping_session','is_group','shared','shared_to_me')
PRESERVED_NATIVE=('flagged','flag_state','tags','tags_complete','section','section_status','early_alerts','early_payload_fingerprint')


def list_scope(adapter,source,identity):
    if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Subtree moves require the explicitly selected ReminderKit backend')
    values=[v for v in adapter.lists() if v.get('id')==identity and v.get('source_id')==source]
    if len(values)!=1 or values[0].get('writable') is not True:raise SafetyError('Move requires an exact writable list in the selected source')
    role=adapter.reader.read('list_features',list_id=identity)
    if (role.get('list_id')!=identity or role.get('source_id')!=source or role.get('data_mutated') is not False
            or any(role.get(key) is not False for key in ROLE_FLAGS)):
        raise SafetyError('Move requires unshared physical nongroup nongrocery lists')
    return {'list':values[0],'role':{key:role[key] for key in ('list_id','source_id',*ROLE_FLAGS)}}


def structure(graph):return [{'id':v['id'],'parent_id':v['parent_id']} for v in graph['nodes']]


def subtree(graph,identity):
    nodes={v['id']:v for v in graph['nodes']}
    if identity not in nodes or nodes[identity]['parent_id'] is not None:raise SafetyError('Select one root reminder; this move keeps all its descendants together')
    result=[identity]
    for parent in result:
        result.extend(v['id'] for v in graph['nodes'] if v['parent_id']==parent)
        if len(result)>20:raise SafetyError('One move supports at most 20 reminders including descendants')
    return result


def check_item_graph(snapshot,graph,identity):
    node=next((v for v in graph['nodes'] if v['id']==identity),None);item=snapshot.get('item',{});native=snapshot.get('native_metadata')
    if (node is None or item.get('id')!=identity or item.get('source_id')!=graph['source_id'] or item.get('list_id')!=graph['list_id']
            or item.get('title')!=node['title'] or item.get('completed') is not node['completed']
            or item.get('public_metadata_complete') is not True or item.get('has_recurrence') is not False
            or not isinstance(native,dict) or any(key not in native for key in PRESERVED_NATIVE)
            or type(snapshot.get('urgent')) is not bool or native.get('parent_id')!=node['parent_id']
            or native.get('is_subtask') is not (node['parent_id'] is not None)):
        raise SafetyError('Exact public/native reminder and list hierarchy disagree or require unsupported recurrence semantics')
    children=sorted(v['id'] for v in graph['nodes'] if v['parent_id']==identity)
    if native.get('child_ids') is not None and sorted(native['child_ids'])!=children:raise SafetyError('Direct-child view and complete list graph disagree')
    if native.get('has_subtasks') is not None and native['has_subtasks'] is not bool(children):raise SafetyError('Child-presence view and complete list graph disagree')
    if native['section'] is not None or native['section_status']!='provider_not_reported':raise SafetyError('Sectioned reminders need an explicit section migration policy before moving')


def snapshot(adapter,item):
    if not isinstance(item.get('list_id'),str) or not item['list_id']:raise SafetyError('A current move requires the bound original list and complete hierarchy')
    source=item['source_id'];origin=item['list_id'];destination=item['fields']['list_id']
    if origin==destination:raise SafetyError('Reminder is already in the requested list')
    origin_scope=list_scope(adapter,source,origin);destination_scope=list_scope(adapter,source,destination)
    first=adapter.reader.read('list_hierarchy',list_id=origin);second=adapter.reader.read('list_hierarchy',list_id=destination)
    if first['source_id']!=source or second['source_id']!=source:raise SafetyError('Move graph belongs to another source')
    identities=subtree(first,item['id']);snapshots=[]
    if len(second['nodes'])+len(identities)>200:raise SafetyError('Destination hierarchy would exceed the verified 200-object bound')
    for identity in identities:
        observed=adapter.snapshot({'operation':'update_reminder','source_id':source,'id':identity,'fields':{}})
        check_item_graph(observed,first,identity);snapshots.append(observed)
    before=dict(snapshots[0]);before.update(source_scope=origin_scope,destination_scope=destination_scope,
        source_graph=first,destination_graph=second,descendants=snapshots[1:])
    return before


def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','id','fields'}
            or change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['fields'],dict)
            or set(change['fields'])!={'list_id'}):raise SafetyError('Move requires an exact reminder, selected source and destination list')
    for value in (change['id'],change['fields']['list_id']):
        if not isinstance(value,str) or not 1<=len(value)<=1000:raise SafetyError('Use exact saved reminder and list IDs')
    if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Subtree moves require the explicitly selected ReminderKit backend')
    current=adapter.reader.read('get',id=change['id'])['item']
    if current.get('id')!=change['id'] or current.get('source_id')!=change['source_id']:raise SafetyError('Move reminder is outside the selected source')
    item={key:deepcopy(change[key]) for key in ('operation','source_id','id','fields')};item['list_id']=current['list_id']
    item['before']=snapshot(adapter,item);affected=[item['before']['item'],*[v['item'] for v in item['before']['descendants']]]
    item.update(fidelity_verified=True,effects={'change':'Move this root and every reviewed descendant as existing native objects to the exact destination list',
        'affected_objects':[{'id':v['id'],'title':v['title']} for v in affected],
        'quota_objects':len(affected),'hierarchy':'Keep parent/child IDs together. Subtask detachment, section migration and recurring reminders are refused.',
        'preservation':'Exact exposed public fields, flags, tags, urgent state, early reminders and hierarchy are independently verified; no content rewrite.',
        'ordering':'Append the root at the destination. Exact native display ordering and unexposed metadata remain unverified.',
        'notifications':'Apple controls synchronization and notification delivery; no date, alarm or completion change is requested.'})
    return item


def expected_graphs(item):
    before=item['before'];moving=set(subtree(before['source_graph'],item['id']));origin=deepcopy(before['source_graph']);destination=deepcopy(before['destination_graph'])
    moved=[v for v in origin['nodes'] if v['id'] in moving];origin['nodes']=[v for v in origin['nodes'] if v['id'] not in moving]
    destination['nodes']=sorted(destination['nodes']+moved,key=lambda v:v['id'])
    return origin,destination


def verify(adapter,item,outcome):
    before=item['before'];destination=item['fields']['list_id'];expected_source,expected_destination=expected_graphs(item)
    identities=subtree(before['source_graph'],item['id'])
    if (outcome.get('id')!=item['id'] or outcome.get('source_id')!=item['source_id'] or outcome.get('list_id')!=destination
            or outcome.get('moved_ids')!=sorted(identities)):return False
    if list_scope(adapter,item['source_id'],item['list_id'])!=before['source_scope'] or list_scope(adapter,item['source_id'],destination)!=before['destination_scope']:return False
    if adapter.reader.read('list_hierarchy',list_id=item['list_id'])!=expected_source or adapter.reader.read('list_hierarchy',list_id=destination)!=expected_destination:return False
    for previous in [before,*before['descendants']]:
        identity=previous['item']['id'];observed=adapter.snapshot({'operation':'update_reminder','source_id':item['source_id'],'id':identity,'fields':{}})
        check_item_graph(observed,expected_destination,identity)
        expected=dict(previous['item'],list_id=destination);actual=observed['item']
        if {k:v for k,v in expected.items() if k!='modified'}!={k:v for k,v in actual.items() if k!='modified'}:return False
        if any(previous['native_metadata'][key]!=observed['native_metadata'][key] for key in PRESERVED_NATIVE) or previous['urgent'] is not observed['urgent']:return False
    return True


def charge(item):
    before=item.get('before',{});identities=subtree(before['source_graph'],item['id']);snapshots=[before,*before.get('descendants',[])]
    if [v.get('item',{}).get('id') for v in snapshots]!=identities:raise SafetyError('Move quota does not bind the complete reviewed subtree')
    return len(identities)


def observe_unchanged(adapter,item,root):
    """Read all reviewed objects and both graphs; never settle or replay a move."""
    before=item['before'];identities=subtree(before['source_graph'],item['id'])
    if charge(item)!=len(identities):raise SafetyError('Incomplete original move snapshot')
    matches=root=={key:before[key] for key in ('list','item','native_metadata','urgent')}
    for previous in before['descendants']:
        observed=adapter.snapshot({'operation':'update_reminder','source_id':item['source_id'],'id':previous['item']['id'],'fields':{}})
        matches=(observed==previous) and matches
    for identity,key in ((item['list_id'],'source'),(item['fields']['list_id'],'destination')):
        matches=(list_scope(adapter,item['source_id'],identity)==before[key+'_scope']) and matches
        matches=(adapter.reader.read('list_hierarchy',list_id=identity)==before[key+'_graph']) and matches
    return matches
