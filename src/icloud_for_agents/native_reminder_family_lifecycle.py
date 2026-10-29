"""Explicit root-family recovery, bound to every original native identity.

Kept separate from the established leaf path. Native hierarchy order and manual
ordering are distinct; recovery does not promise the original manual positions.
"""
from copy import deepcopy

from .core import SafetyError, NativeObjectNotFound
from .native_reminder_moves import list_scope, subtree, check_item_graph

MAX_FAMILY = 10


def selected(item):
    return isinstance(item.get('fields'), dict) and item['fields'].get('include_descendants') is True


def members(before, identity):
    graph = before['source_graph']
    ids = subtree(graph, identity)
    if not 2 <= len(ids) <= MAX_FAMILY:
        raise SafetyError('Select a root with 1 to 9 reviewed direct children; use ordinary removal for a leaf')
    if any(node['id'] in ids[1:] and node['parent_id'] != identity for node in graph['nodes']):
        raise SafetyError('Family recovery currently requires direct children; nested descendants are unsupported')
    snapshots = [before, *before.get('descendants', [])]
    if [value.get('item', {}).get('id') for value in snapshots] != ids:
        raise SafetyError('Every affected family object must have its exact ordered before snapshot')
    for value, target in zip(snapshots, ids):
        check_item_graph(value, graph, target)
        children = sorted(node['id'] for node in graph['nodes'] if node['parent_id'] == target)
        if value['native_metadata'].get('child_ids') != children:
            raise SafetyError('Exact direct-child inventories are required for family recovery')
    return ids


def deletion_snapshot(adapter, item):
    source = item['source_id'];identity = item['id'];container = item['list_id']
    scope = list_scope(adapter, source, container)
    graph = adapter.reader.read('list_hierarchy', list_id=container)
    if graph.get('source_id') != source or graph.get('list_id') != container:
        raise SafetyError('The complete bounded source hierarchy is required')
    ids = subtree(graph, identity)
    if not 2 <= len(ids) <= MAX_FAMILY:
        raise SafetyError('Family removal supports one root and at most nine direct children')
    snapshots = [adapter.snapshot({'operation':'update_reminder','source_id':source,'id':target,'fields':{}}) for target in ids]
    value = dict(snapshots[0], source_scope=scope, source_graph=graph, descendants=snapshots[1:])
    members(value, identity)
    recovery = [adapter.reader.read('recovery_lookup', source_id=source, id=target) for target in ids]
    if any(row.get('id') != target or row.get('source_id') != source or row.get('found_in_view') is not False for target,row in zip(ids,recovery)):
        raise SafetyError('Every family member must be absent from the selected recovery view')
    value.update(recovery=recovery[0], family_recovery=recovery)
    if adapter.reader.read('list_hierarchy', list_id=container) != graph:
        raise SafetyError('Reminder hierarchy changed during family review')
    return value


def prepare_delete(adapter, change):
    from .native_reminder_lifecycle import advanced
    if (set(change) != {'operation','source_id','id','fields'} or change.get('operation') != 'delete_reminder'
            or change.get('fields') != {'recoverable':True,'include_descendants':True}
            or any(type(change['fields'].get(key)) is not bool for key in ('recoverable','include_descendants'))):
        raise SafetyError('Family removal requires explicit recoverable and include_descendants booleans')
    advanced(change['source_id'])
    current = adapter.reader.read('get', id=change['id'])['item']
    if current.get('id') != change['id'] or current.get('source_id') != change['source_id']:
        raise SafetyError('Family root is outside the exact selected source')
    item = deepcopy(change);item['list_id'] = current['list_id'];item['before'] = deletion_snapshot(adapter,item)
    ids = members(item['before'], item['id'])
    item.update(fidelity_verified=True,effects={
        'removal':'Move this root and all explicitly reviewed direct children into provider Recently Deleted; never permanently delete.',
        'affected_objects':ids,'quota_objects':len(ids),
        'recovery':'Separate approved restoration must reference this completed owned deletion ledger and explicitly include descendants. Apple controls retention and synchronization.',
        'preservation':'Require every reviewed native identity/title/parent relation in recovery and absence from the bounded source graph. Full exposed content is verified after restoration.',
        'limits':'No nested, recurring, sectioned, shared or grocery family. Manual order and notification delivery are not preserved guarantees.'})
    return item


def recovery_family(adapter, source, identity, original):
    ids = members(original, identity)
    lookups = [adapter.reader.read('recovery_lookup', source_id=source, id=target) for target in ids]
    tree = adapter.reader.read('recovery_tree', source_id=source, id=identity)
    value = {'lookup':lookups[0], 'tree':tree, 'members':lookups}
    if not recovery_matches(value, original, identity, source):
        raise SafetyError('Recovery must report exactly the reviewed native family, titles and parent relationships')
    return value


def recovery_matches(value, original, identity, source):
    try:
        ids = members(original, identity);lookups = value['members'];tree = value['tree']
        expected = {row['id']:(row['parent_id'],row['title']) for row in original['source_graph']['nodes'] if row['id'] in ids}
        nodes = tree['nodes']
        return (value['lookup'] == lookups[0] and len(lookups) == len(ids)
                and tree.get('source_id') == source and tree.get('requested_id') == identity and tree.get('root_id') == identity
                and len(nodes) == len(ids) and len({row['id'] for row in nodes}) == len(ids)
                and {row['id']:(row['parent_id'],row['title']) for row in nodes} == expected
                and all(row.get('id') == target and row.get('source_id') == source and row.get('found_in_view') is True
                        and row.get('recovery_root_id') == identity and row.get('found_as_root') is (target == identity)
                        and row.get('parent_id') == (None if target == identity else identity) for target,row in zip(ids,lookups)))
    except (KeyError,TypeError,AttributeError,SafetyError):
        return False


def restoration_snapshot(adapter, item):
    from .native_reminder_lifecycle import original_deletion
    original, proof = original_deletion(adapter,item,family=True)
    source = item['source_id'];identity = item['id'];destination = item['list_id'];baseline = original['before'];ids = members(baseline,identity)
    if destination != baseline['item']['list_id']:
        raise SafetyError('Family recovery currently requires the original physical list; move it separately after restoration')
    scope = list_scope(adapter,source,destination);graph = adapter.reader.read('list_hierarchy',list_id=destination)
    if graph.get('source_id') != source or graph.get('list_id') != destination or len(graph['nodes'])+len(ids) > 200 or any(row['id'] in ids for row in graph['nodes']):
        raise SafetyError('Destination already contains a family ID or exceeds the bounded hierarchy scope')
    for target in ids:
        try:adapter.reader.read('get',id=target)
        except NativeObjectNotFound:pass
        else:raise SafetyError('A family member already exists in selected live scope')
    return {'original':deepcopy(baseline),'deletion_proof':proof,'destination_scope':scope,'destination_graph':graph,
            'recovery':recovery_family(adapter,source,identity,baseline)}


def prepare_restore(adapter, change):
    from .native_reminder_lifecycle import advanced
    if (set(change) != {'operation','source_id','id','fields'} or change.get('operation') != 'restore_reminder'
            or set(change['fields'])-{'deletion_plan_id','deletion_index','list_id','include_descendants'}
            or not {'deletion_plan_id','list_id','include_descendants'} <= set(change['fields'])
            or change['fields'].get('include_descendants') is not True):
        raise SafetyError('Family restore requires explicit include_descendants and the owned deletion reference/destination')
    advanced(change['source_id']);fields = change['fields'];index = fields.get('deletion_index',0)
    if type(index) is not int or not 0 <= index < 20 or any(not isinstance(v,str) or not 1 <= len(v) <= 1000 for v in (change['id'],fields['deletion_plan_id'],fields['list_id'])):
        raise SafetyError('Invalid family restoration identity or deletion reference')
    item = deepcopy(change);item['fields']['deletion_index'] = index;item['list_id'] = fields['list_id'];item['before'] = restoration_snapshot(adapter,item)
    ids = members(item['before']['original'],item['id'])
    item.update(fidelity_verified=True,effects={'restoration':'Restore exactly the original native family from the completed owned deletion ledger into the reviewed private list.',
        'affected_objects':ids,'quota_objects':len(ids),'preservation':'Verify original identities, content, completion and native metadata plus parent/child relationships; never recreate copies.',
        'manual_order':'Native recovery may change underlying manual positions. This restore does not promise their original sequence.',
        'notifications':'Existing due dates and alarms can become active again. Apple controls delivery and synchronization.'})
    return item


def verify_delete(adapter, item, outcome, *, details=False):
    if outcome.get('id') != item['id'] or outcome.get('source_id') != item['source_id']:return False
    before = item['before'];ids = members(before,item['id']);graph = deepcopy(before['source_graph']);graph['nodes'] = [row for row in graph['nodes'] if row['id'] not in ids]
    scope = list_scope(adapter,item['source_id'],item['list_id']);observed = adapter.reader.read('list_hierarchy',list_id=item['list_id'])
    if scope != before['source_scope'] or observed != graph:return False
    for target in ids:
        try:adapter.reader.read('get',id=target)
        except NativeObjectNotFound:pass
        else:return False
    recovery = recovery_family(adapter,item['source_id'],item['id'],before)
    return {'state':'in_recovery','id':item['id'],'source_id':item['source_id'],'scope':scope,'graph':observed,'recovery':recovery} if details else True


def verify_restore(adapter, item, outcome, *, details=False):
    from .native_reminder_lifecycle import original_deletion
    if any(outcome.get(key) != item[key] for key in ('id','source_id','list_id')):return False
    original, proof = original_deletion(adapter,item,family=True);before = item['before'];baseline = original['before'];ids = members(baseline,item['id'])
    if proof != before['deletion_proof'] or list_scope(adapter,item['source_id'],item['list_id']) != before['destination_scope']:return False
    expected = deepcopy(before['destination_graph']);expected['nodes'] = sorted(expected['nodes']+[row for row in baseline['source_graph']['nodes'] if row['id'] in ids],key=lambda row:row['id'])
    graph = adapter.reader.read('list_hierarchy',list_id=item['list_id'])
    if graph != expected:return False
    snapshots = [adapter.snapshot({'operation':'update_reminder','source_id':item['source_id'],'id':target,'fields':{}}) for target in ids]
    lookups = [adapter.reader.read('recovery_lookup',source_id=item['source_id'],id=target) for target in ids]
    value = {'state':'restored','id':item['id'],'source_id':item['source_id'],'snapshots':snapshots,'graph':graph,'recovery_members':lookups}
    if not record_matches(item,value):return False
    return value if details else True


def record_matches(item, observed):
    try:
        if item.get('operation') not in {'delete_reminder','restore_reminder'}:return False
        if observed.get('id') != item['id'] or observed.get('source_id') != item['source_id']:return False
        before = item['before']
        if item['operation'] == 'delete_reminder':
            ids = members(before,item['id']);expected = deepcopy(before['source_graph']);expected['nodes'] = [row for row in expected['nodes'] if row['id'] not in ids]
            return (observed.get('state') == 'in_recovery' and observed.get('scope') == before['source_scope'] and observed.get('graph') == expected
                    and recovery_matches(observed['recovery'],before,item['id'],item['source_id']))
        baseline = before['original'];ids = members(baseline,item['id']);expected = deepcopy(before['destination_graph'])
        expected['nodes'] = sorted(expected['nodes']+[row for row in baseline['source_graph']['nodes'] if row['id'] in ids],key=lambda row:row['id'])
        snapshots = observed['snapshots'];previous = [baseline,*baseline['descendants']];lookups = observed['recovery_members']
        if observed.get('state') != 'restored' or observed.get('graph') != expected or len(snapshots) != len(ids) or len(lookups) != len(ids):return False
        for target,old,current,recovery in zip(ids,previous,snapshots,lookups):
            check_item_graph(current,expected,target)
            if (current.get('list') != before['destination_scope']['list'] or current.get('urgent') is not old['urgent']
                    or {k:v for k,v in current['item'].items() if k != 'modified'} != {k:v for k,v in dict(old['item'],list_id=item['list_id']).items() if k != 'modified'}
                    or dict(old['native_metadata'],list_id=item['list_id']) != current['native_metadata']
                    or recovery.get('id') != target or recovery.get('source_id') != item['source_id'] or recovery.get('found_in_view') is not False):return False
        return True
    except (KeyError,TypeError,AttributeError,SafetyError):return False


def charge(item):
    before = item['before']['original'] if item['operation'] == 'restore_reminder' else item['before']
    if (before['item']['id'] != item['id'] or before['item']['source_id'] != item['source_id']
            or before['source_graph']['source_id'] != item['source_id'] or before['item']['list_id'] != item['list_id']):
        raise SafetyError('Family quota requires the exact reviewed source, original list and root identity')
    return len(members(before,item['id']))
