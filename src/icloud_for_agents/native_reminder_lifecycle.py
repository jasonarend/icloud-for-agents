"""Exact recoverable removal and restoration of previously reviewed root leaves.

Restoration requires this owner's completed deletion ledger, not caller-supplied
before data. Provider recovery views remain bounded separate observations.
"""
from copy import deepcopy
from .core import SafetyError,NativeObjectNotFound,digest
from .native_once import planning_access
from .native_read import native_config
from .native_reminder_moves import list_scope,subtree,check_item_graph,PRESERVED_NATIVE


def advanced(source):
    if source not in planning_access('reminders')['source_ids'] or native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Select an exact Reminder source and the ReminderKit backend')


def leaf_graph(adapter,source,list_id,identity):
    scope=list_scope(adapter,source,list_id);graph=adapter.reader.read('list_hierarchy',list_id=list_id)
    if graph.get('source_id')!=source or graph.get('list_id')!=list_id or subtree(graph,identity)!=[identity]:raise SafetyError('Recoverable removal currently requires one root reminder with no descendants')
    return scope,graph


def deletion_snapshot(adapter,item):
    source=item['source_id'];identity=item['id'];scope,graph=leaf_graph(adapter,source,item['list_id'],identity)
    observed=adapter.snapshot({'operation':'update_reminder','source_id':source,'id':identity,'fields':{}});check_item_graph(observed,graph,identity)
    if observed['native_metadata'].get('child_ids')!=[] or observed['native_metadata'].get('is_subtask') is not False:raise SafetyError('Exact nonnested leaf metadata is required before removal')
    recovery=adapter.reader.read('recovery_lookup',source_id=source,id=identity)
    if recovery.get('found_in_view') is not False:raise SafetyError('Reminder already appears in recovery or recovery support is unavailable')
    return dict(observed,source_scope=scope,source_graph=graph,recovery=recovery)


def prepare_delete(adapter,change):
    if not isinstance(change,dict) or set(change)!={'operation','source_id','id','fields'} or change.get('operation')!='delete_reminder':raise SafetyError('Use an exact source, reminder and recoverable removal field')
    source=change['source_id'];advanced(source)
    if change['fields']!={'recoverable':True} or type(change['fields'].get('recoverable')) is not bool:raise SafetyError('Removal requires fields.recoverable true; permanent deletion is unavailable')
    if not isinstance(change['id'],str) or not 1<=len(change['id'])<=1000:raise SafetyError('Select an exact saved reminder ID')
    current=adapter.reader.read('get',id=change['id'])['item']
    if current.get('id')!=change['id'] or current.get('source_id')!=source:raise SafetyError('Removal target is outside the exact selected source')
    item=deepcopy(change);item['list_id']=current['list_id'];item['before']=deletion_snapshot(adapter,item);item.update(fidelity_verified=True,effects={
        'removal':'Remove this existing native root leaf into the provider Recently Deleted view. Never permanently delete or empty recovery.',
        'recovery':'Restoration requires a separate approved restore_reminder referencing this completed deletion plan. Apple controls recovery retention and synchronization.',
        'scope':'Nested, recurring, sectioned reminders and roots with descendants are refused. One exact native reminder counts against scope and quotas.',
        'verification':'Require source graph absence and the same ID/title in recovery. Unexposed recovery content remains unverified until restoration.'})
    return item


def original_deletion(adapter,item):
    store=getattr(adapter,'_plan_store',None);owner=getattr(adapter,'_plan_owner',None)
    if store is None or not owner:raise SafetyError('Restoration requires the owner-bound deletion ledger')
    fields=item['fields'];identity=fields['deletion_plan_id'];index=fields['deletion_index'];plan=store.read(store.path(identity,'.json'));result=store.read(store.path(identity,'.result'));started=store.read(store.path(identity,'.started'));payload=plan.get('payload',{});changes=payload.get('changes')
    if (plan.get('kind')!='reminders_change' or payload.get('account')!=owner or not isinstance(changes,list) or not 0<=index<len(changes)
            or started.get('sha256')!=digest(plan) or started.get('account')!=owner
            or not (store.operation_settled(identity) or store._native_reconciliation_completed(identity) or store._native_lifecycle_readback_completed(identity))):raise SafetyError('Select a completed owned native deletion plan and exact item index')
    original=changes[index];attempts=result.get('attempted');completed=result.get('completed');terminal_readback=store._native_lifecycle_readback_completed(identity)
    if original.get('operation')!='delete_reminder' or original.get('id')!=item['id'] or original.get('source_id')!=item['source_id'] or original.get('fields')!={'recoverable':True}:raise SafetyError('Restoration identity or source does not match the deletion ledger')
    if terminal_readback:
        if index!=0 or len(changes)!=1:raise SafetyError('Terminal lifecycle readback binds only one exact original target')
    else:
        if (not isinstance(attempts,list) or not index<len(attempts)<=len(changes)
                or any(a.get('index')!=i or a.get('operation')!=changes[i].get('operation') for i,a in enumerate(attempts))
                or (result.get('status')=='applied' and (completed!=attempts or len(attempts)!=len(changes) or result.get('plan_sha256')!=digest(plan)))
                or (result.get('status') in {'stopped_before_dispatch','refused_without_dispatch'} and (not isinstance(completed,list) or index>=len(completed) or completed[index]!=attempts[index]))):
            raise SafetyError('Restoration deletion completion does not match the ledger')
        attempt=attempts[index]
        if (attempt.get('result',{}).get('id')!=item['id'] or store.read(store.path(identity,'.attempt-'+str(index)))!={'plan_sha256':digest(plan),'attempt':attempt}):raise SafetyError('Original deletion attempt is not bound to its saved exact ID')
    if 'standing_grant' in payload:
        authority=payload['standing_grant'];use=store.read(store.path(identity,'.native-grant-use'))
        if (started.get('authorization')!='standing-native-grant' or started.get('grant_id')!=authority.get('grant_id') or started.get('grant_sha256')!=authority.get('sha256')
                or use.get('plan_sha256')!=digest(plan) or use.get('grant_id')!=authority.get('grant_id') or use.get('grant_sha256')!=authority.get('sha256')):raise SafetyError('Original standing deletion admission is inconsistent')
    else:
        policy=payload.get('context',{}).get('approval_policy');expected='local-receipt' if policy=='local-receipt' else 'trusted-client-policy; not an independent human receipt' if policy=='trusted-client' else None
        if expected is None or started.get('authorization')!=expected:raise SafetyError('Original deletion approval admission is invalid')
        if policy=='local-receipt':
            receipt=store.read(store.path(identity,'.approved'))
            if receipt.get('plan_id')!=identity or receipt.get('sha256')!=digest(plan):raise SafetyError('Original exact deletion receipt is unavailable or mismatched')
    before=original.get('before',{});scope,graph=before.get('source_scope'),before.get('source_graph')
    if not isinstance(scope,dict) or not isinstance(graph,dict) or subtree(graph,item['id'])!=[item['id']]:raise SafetyError('Original deletion does not bind an exact root leaf')
    check_item_graph(before,graph,item['id'])
    return original,{'plan_id':identity,'index':index,'plan_sha256':digest(plan),'result_sha256':digest(result),'item_sha256':digest(original),
                     'completion_evidence':'known_target_terminal_error_and_independent_expected_state' if terminal_readback else 'returned_native_id_and_verified_readback',
                     'lifecycle_readback_sha256':digest(store.read(store.path(identity,'.native-lifecycle-readback'))) if terminal_readback else None}


def recovery_leaf(adapter,source,identity,expected):
    lookup=adapter.reader.read('recovery_lookup',source_id=source,id=identity);tree=adapter.reader.read('recovery_tree',source_id=source,id=identity)
    if (lookup.get('found_in_view') is not True or lookup.get('found_as_root') is not True or lookup.get('recovery_root_id')!=identity
            or lookup.get('parent_id') is not None or tree.get('requested_id')!=identity or tree.get('root_id')!=identity
            or tree.get('source_id')!=source or not isinstance(tree.get('nodes'),list) or len(tree['nodes'])!=1):raise SafetyError('Recovery must report this exact originally reviewed root leaf without additional nodes')
    node=tree['nodes'][0]
    if node.get('id')!=identity or node.get('parent_id') is not None or node.get('title')!=expected['title']:raise SafetyError('Deleted reminder identity or title changed')
    return {'lookup':lookup,'tree':tree}


def restoration_snapshot(adapter,item):
    original,proof=original_deletion(adapter,item);source=item['source_id'];identity=item['id'];destination=item['fields']['list_id'];scope=list_scope(adapter,source,destination);graph=adapter.reader.read('list_hierarchy',list_id=destination)
    if graph.get('source_id')!=source or any(node['id']==identity for node in graph['nodes']) or len(graph['nodes'])>=200:raise SafetyError('Restore destination membership is outside scope, already contains the ID or exceeds bounds')
    try:adapter.reader.read('get',id=identity)
    except NativeObjectNotFound:pass
    else:raise SafetyError('Reminder already exists in selected live scope; no restore')
    recovery=recovery_leaf(adapter,source,identity,original['before']['item'])
    return {'original':deepcopy(original['before']),'deletion_proof':proof,'destination_scope':scope,'destination_graph':graph,'recovery':recovery}


def prepare_restore(adapter,change):
    if not isinstance(change,dict) or set(change)!={'operation','source_id','id','fields'} or change.get('operation')!='restore_reminder':raise SafetyError('Use exact source, deleted reminder and its original deletion reference')
    advanced(change['source_id']);fields=change['fields']
    if not isinstance(fields,dict) or set(fields)-{'deletion_plan_id','deletion_index','list_id'} or not {'deletion_plan_id','list_id'}<=set(fields):raise SafetyError('Restore requires an owned deletion_plan_id, optional deletion_index and exact destination list_id')
    index=fields.get('deletion_index',0)
    if type(index) is not int or not 0<=index<20 or any(not isinstance(value,str) or not 1<=len(value)<=1000 for value in (change['id'],fields['deletion_plan_id'],fields['list_id'])):raise SafetyError('Invalid exact deletion reference or restore destination')
    item=deepcopy(change);item['fields']['deletion_index']=index;item['list_id']=fields['list_id'];item['before']=restoration_snapshot(adapter,item);item.update(fidelity_verified=True,effects={
        'restoration':'Restore only this existing native ID from the completed owned deletion ledger to the exact private ordinary destination list.',
        'content':'No title, notes, alarm, tag or body rewrite; independent readback requires original exposed fields and native metadata.',
        'scope':'Originally deleted root leaves only. Additional recovery nodes, live duplicates, wrong owner/source, missing ledger evidence or unsupported provider state refuse.',
        'notifications':'Restoring can reactivate existing alarms or due notifications. Apple controls delivery and synchronization.'})
    return item


def verify_delete(adapter,item,outcome,*,details=False):
    if outcome.get('id')!=item['id'] or outcome.get('source_id')!=item['source_id']:return False
    before=item['before'];expected=deepcopy(before['source_graph']);expected['nodes']=[node for node in expected['nodes'] if node['id']!=item['id']]
    current_scope=list_scope(adapter,item['source_id'],item['list_id']);current_graph=adapter.reader.read('list_hierarchy',list_id=item['list_id'])
    if current_scope!=before['source_scope'] or current_graph!=expected:return False
    try:adapter.reader.read('get',id=item['id'])
    except NativeObjectNotFound:pass
    else:return False
    recovery=recovery_leaf(adapter,item['source_id'],item['id'],before['item'])
    return {'state':'in_recovery','id':item['id'],'source_id':item['source_id'],'scope':current_scope,'graph':current_graph,'recovery':recovery} if details else True


def verify_restore(adapter,item,outcome,*,details=False):
    if outcome.get('id')!=item['id'] or outcome.get('source_id')!=item['source_id'] or outcome.get('list_id')!=item['list_id']:return False
    original,proof=original_deletion(adapter,item);before=item['before']
    if proof!=before['deletion_proof'] or list_scope(adapter,item['source_id'],item['list_id'])!=before['destination_scope']:return False
    expected=deepcopy(before['destination_graph']);expected['nodes']=sorted(expected['nodes']+[{'id':item['id'],'parent_id':None,'title':original['before']['item']['title'],'completed':original['before']['item']['completed']}],key=lambda row:row['id'])
    current_graph=adapter.reader.read('list_hierarchy',list_id=item['list_id'])
    if current_graph!=expected:return False
    observed=adapter.snapshot({'operation':'update_reminder','source_id':item['source_id'],'id':item['id'],'fields':{}});baseline=original['before'];expected_item=dict(baseline['item'],list_id=item['list_id'])
    if {k:v for k,v in observed['item'].items() if k!='modified'}!={k:v for k,v in expected_item.items() if k!='modified'}:return False
    native=observed.get('native_metadata',{})
    if (native.get('is_subtask') is not False or native.get('parent_id') is not None or native.get('child_ids')!=[]
            or any(native.get(key)!=baseline['native_metadata'].get(key) for key in PRESERVED_NATIVE) or observed.get('urgent') is not baseline['urgent']):return False
    recovery=adapter.reader.read('recovery_lookup',source_id=item['source_id'],id=item['id'])
    if recovery.get('found_in_view') is not False:return False
    return {'state':'restored','id':item['id'],'source_id':item['source_id'],'snapshot':observed,'graph':current_graph,'recovery':recovery} if details else True


def reconcile(engine,identity):
    """Verify the expected current state after a retained terminal error; never retry."""
    from .native_reminder_exchange import inspect
    context=engine.context(True)
    with engine.serialized():
        plan=engine.store.read(engine.store.path(identity,'.json'));result=engine.store.read(engine.store.path(identity,'.result'));payload=plan.get('payload',{});items=payload.get('changes',[]);started=engine.store.read(engine.store.path(identity,'.started'))
        if (plan.get('kind')!='reminders_change' or payload.get('account')!=engine.account or not isinstance(items,list) or len(items)!=1
                or items[0].get('operation') not in {'delete_reminder','restore_reminder','reorder_reminder','create_group','rename_group','group_list','ungroup_list','reorder_group'} or result.get('status')!='failed_or_uncertain'
                or result.get('phase')!='mutation' or result.get('attempted')!=[] or result.get('completed')!=[]
                or started.get('sha256')!=digest(plan) or started.get('account')!=engine.account
                or payload.get('context',{}).get('source_ids')!=context['source_ids'] or payload.get('context',{}).get('backend')!=context['backend']):
            raise SafetyError('Lifecycle reconciliation requires one owned known-ID terminal-error attempt with unchanged source/backend')
        terminal=inspect(engine.store,plan);request=engine.store.read(engine.store.path(identity,'.native-reminder-request-0'));response=engine.store.read(engine.store.path(identity,'.native-reminder-response-0'))
        if terminal.get('terminal_response')!='recorded' or terminal.get('helper_reported_mutation_dispatched') is False or response.get('response_kind')!='error':raise SafetyError('Retained request-bound terminal error required; missing evidence is never reconstructed')
        item=items[0];outcome={'id':item['id'],'source_id':item['source_id'],'list_id':item.get('list_id')}
        if item['operation']=='reorder_group':
            from .native_group_order import expected_observation
            observed=expected_observation(engine.adapter,item)
        elif item['operation'] in {'group_list','ungroup_list'}:
            from .native_list_groups import expected_observation
            observed=expected_observation(engine.adapter,item)
        elif item['operation'] in {'create_group','rename_group'}:
            from .native_reminder_groups import expected_observation
            observed=expected_observation(engine.adapter,item)
        elif item['operation']=='reorder_reminder':
            from .native_reminder_order import expected_observation
            observed=expected_observation(engine.adapter,item)
        else:
            verify=verify_delete if item['operation']=='delete_reminder' else verify_restore;observed=verify(engine.adapter,item,outcome,details=True)
        if not isinstance(observed,dict) or not expected_record_matches(item,observed) or engine.context(True)!=context:raise SafetyError('Expected lifecycle state does not independently verify; original uncertainty remains')
        engine.store.write_once(engine.store.path(identity,'.native-lifecycle-readback'),{'status':'native_lifecycle_expected_state_observed','plan_sha256':digest(plan),'result_sha256':digest(result),
            'request_record_sha256':digest(request),'response_record_sha256':digest(response),'item_sha256':digest(item),'observed':observed,'observed_sha256':digest(observed),
            'original_failure_preserved':True,'causal_dispatch_proven':False,'do_not_retry':True})
        return engine.store.status(identity)


def expected_record_matches(item,observed):
    """Pure binding checks for persisted readback evidence; no native access."""
    try:
        if not isinstance(observed,dict) or observed.get('id')!=item['id'] or observed.get('source_id')!=item['source_id']:return False
        if item['operation']=='reorder_group':
            from .native_group_order import record_matches
            return record_matches(item,observed)
        if item['operation'] in {'group_list','ungroup_list'}:
            from .native_list_groups import record_matches
            return record_matches(item,observed)
        if item['operation'] in {'create_group','rename_group'}:
            from .native_reminder_groups import record_matches
            return record_matches(item,observed)
        if item['operation']=='reorder_reminder':
            from .native_reminder_order import record_matches
            return record_matches(item,observed)
        before=item['before'];identity=item['id'];source=item['source_id']
        if item['operation']=='delete_reminder':
            expected=deepcopy(before['source_graph']);expected['nodes']=[node for node in expected['nodes'] if node['id']!=identity];recovery=observed['recovery'];lookup=recovery['lookup'];tree=recovery['tree'];nodes=tree['nodes']
            return (observed.get('state')=='in_recovery' and observed.get('scope')==before['source_scope'] and observed.get('graph')==expected
                    and lookup.get('id')==identity and lookup.get('source_id')==source and lookup.get('found_in_view') is True and lookup.get('found_as_root') is True
                    and lookup.get('parent_id') is None and lookup.get('recovery_root_id')==identity
                    and tree.get('source_id')==source and tree.get('requested_id')==identity and tree.get('root_id')==identity and len(nodes)==1
                    and nodes[0].get('id')==identity and nodes[0].get('parent_id') is None and nodes[0].get('title')==before['item']['title'])
        if item['operation']!='restore_reminder':return False
        original=before['original'];expected=deepcopy(before['destination_graph']);expected['nodes']=sorted(expected['nodes']+[{'id':identity,'parent_id':None,'title':original['item']['title'],'completed':original['item']['completed']}],key=lambda row:row['id']);snapshot=observed['snapshot'];public=snapshot['item'];native=snapshot['native_metadata'];recovery=observed['recovery'];baseline=dict(original['item'],list_id=item['list_id'])
        return (observed.get('state')=='restored' and observed.get('graph')==expected and snapshot.get('list')==before['destination_scope']['list']
                and {key:value for key,value in public.items() if key!='modified'}=={key:value for key,value in baseline.items() if key!='modified'}
                and all(native.get(key)==value for key,value in {'id':identity,'source_id':source,'list_id':item['list_id'],'is_subtask':False,'parent_id':None,'child_ids':[]}.items())
                and all(native.get(key)==original['native_metadata'].get(key) for key in PRESERVED_NATIVE) and snapshot.get('urgent') is original['urgent']
                and recovery.get('id')==identity and recovery.get('source_id')==source and recovery.get('found_in_view') is False)
    except (KeyError,TypeError,AttributeError):return False


def unchanged_observation(adapter,item):
    """Observe only the exact reviewed live/recovery scope; never retry a write."""
    source=item['source_id'];config=native_config('reminders')
    if source not in config.get('source_ids',[]) or config.get('advanced_backend')!='reminderkit':raise SafetyError('Unchanged lifecycle observation requires the selected source and ReminderKit backend')
    if item['operation']=='delete_reminder':
        current=deletion_snapshot(adapter,item)
        return current==item['before'],'unchanged_root_leaf_and_bounded_source_graph'
    if item['operation']=='restore_reminder':
        current=restoration_snapshot(adapter,item)
        return current==item['before'],'reviewed_recovery_observations_and_bounded_destination_graph'
    raise SafetyError('Expected one exact lifecycle operation')
