"""Read-only observation of one pre-existing reminder after an uncertain change.

An observation is not evidence of whether the original request was dispatched,
and cannot settle or replay it. No title, notes or other contents are returned.
"""
from .core import SafetyError, digest

OBSERVABLE_CHANGES = {'create_smart_list','set_smart_list_sources','rename_smart_list','update_reminder', 'set_completed', 'set_recurrence', 'set_flagged',
                      'remove_tag', 'add_tag', 'replace_tag', 'set_urgent', 'add_early',
                      'clear_early', 'move_reminder', 'create_subtask', 'delete_reminder', 'restore_reminder', 'reorder_reminder', 'create_group', 'rename_group', 'group_list', 'ungroup_list', 'reorder_group', 'set_section', 'reparent_reminder'}


def inspect_uncertain_reminder(engine, plan_id):
    if engine.name != 'reminders':
        raise SafetyError('Reminder observation requires the Reminders service')
    context = engine.context(True)
    plan = engine.store.read(engine.store.path(plan_id, '.json'))
    result = engine.store.read(engine.store.path(plan_id, '.result'))
    payload = plan.get('payload', {})
    items = payload.get('changes', [])
    original_context = payload.get('context', {})
    if (plan.get('kind') != 'reminders_change' or payload.get('account') != engine.account
            or original_context.get('source_ids') != context['source_ids']
            or original_context.get('backend') != context['backend']
            or result.get('status') != 'failed_or_uncertain'
            or result.get('phase') not in {'mutation', 'verification'}
            or not isinstance(items, list) or len(items) != 1
            or engine.store.read(engine.store.path(plan_id, '.started')).get('sha256') != digest(plan)):
        raise SafetyError('Observation requires one owned uncertain change with unchanged source and backend')
    item = items[0]
    if not isinstance(item, dict):
        raise SafetyError('The original reminder snapshot is unavailable')
    before = item.get('before')
    if item.get('operation')=='create_smart_list':
        from .native_reminder_smart_create import snapshot,COVERAGE
        observed=snapshot(engine.adapter,item)
        if engine.context(True)!=context:raise SafetyError('Reminders access changed during custom-view creation observation')
        from .native_reminder_exchange import inspect
        return {'plan_id':plan_id,'reminder_id':item['id'],'source_id':item['source_id'],'observed_snapshot_matches_before':observed==before,
                'snapshot_coverage':COVERAGE,'observation_consistency':'separate_live_reads_may_change','original_uncertainty_retained':True,
                'causal_dispatch_proven':False,'data_mutated':False,'do_not_retry':True,**inspect(engine.store,plan)}
    if item.get('operation')=='set_smart_list_sources':
        from .native_reminder_smart_sources import snapshot,COVERAGE
        observed=snapshot(engine.adapter,item)
        if engine.context(True)!=context:raise SafetyError('Reminders access changed during Smart List source observation')
        from .native_reminder_exchange import inspect
        return {'plan_id':plan_id,'reminder_id':item['id'],'source_id':item['source_id'],'observed_snapshot_matches_before':observed==before,
                'snapshot_coverage':COVERAGE,'observation_consistency':'separate_live_reads_may_change','original_uncertainty_retained':True,
                'causal_dispatch_proven':False,'data_mutated':False,'do_not_retry':True,**inspect(engine.store,plan)}
    if item.get('operation')=='rename_smart_list':
        from .native_reminder_smart_rename import snapshot,COVERAGE
        observed=snapshot(engine.adapter,item)
        if engine.context(True)!=context:raise SafetyError('Reminders access changed during Smart List observation')
        from .native_reminder_exchange import inspect
        return {'plan_id':plan_id,'reminder_id':item['id'],'source_id':item['source_id'],'observed_snapshot_matches_before':observed==before,
                'snapshot_coverage':COVERAGE,'observation_consistency':'separate_live_reads_may_change','original_uncertainty_retained':True,
                'causal_dispatch_proven':False,'data_mutated':False,'do_not_retry':True,**inspect(engine.store,plan)}
    if item.get('operation')=='set_section':
        from .native_reminder_sections import snapshot,COVERAGE
        observed=snapshot(engine.adapter,item)
        if engine.context(True)!=context:raise SafetyError('Reminder access changed during section observation')
        from .native_reminder_exchange import inspect
        return {'plan_id':plan_id,'reminder_id':item['id'],'source_id':item['source_id'],'observed_snapshot_matches_before':observed==before,
                'snapshot_coverage':COVERAGE,'observation_consistency':'separate_live_reads_may_change',
                'original_uncertainty_retained':True,'causal_dispatch_proven':False,'data_mutated':False,'do_not_retry':True,**inspect(engine.store,plan)}
    if item.get('operation')=='reparent_reminder':
        from .native_reminder_parents import snapshot,COVERAGE
        observed=snapshot(engine.adapter,item)
        if engine.context(True)!=context:raise SafetyError('Reminder access changed during parent observation')
        from .native_reminder_exchange import inspect
        return {'plan_id':plan_id,'reminder_id':item['id'],'source_id':item['source_id'],'observed_snapshot_matches_before':observed==before,
                'snapshot_coverage':COVERAGE,'observation_consistency':'separate_live_reads_may_change',
                'original_uncertainty_retained':True,'causal_dispatch_proven':False,'data_mutated':False,'do_not_retry':True,**inspect(engine.store,plan)}
    if item.get('operation')=='reorder_group':
        from .native_group_order import snapshot,COVERAGE
        observed=snapshot(engine.adapter,item)
        if engine.context(True)!=context:raise SafetyError('Reminder access changed during group order observation')
        from .native_reminder_exchange import inspect
        return {'plan_id':plan_id,'reminder_id':item['id'],'source_id':item['source_id'],'observed_snapshot_matches_before':observed==before,
                'snapshot_coverage':COVERAGE,'observation_consistency':'separate_live_reads_may_change',
                'original_uncertainty_retained':True,'causal_dispatch_proven':False,'data_mutated':False,'do_not_retry':True,**inspect(engine.store,plan)}
    if item.get('operation') in {'group_list','ungroup_list'}:
        from .native_list_groups import snapshot
        observed=snapshot(engine.adapter,item)
        if engine.context(True)!=context:raise SafetyError('Reminder access changed during list grouping observation')
        from .native_reminder_exchange import inspect
        return {'plan_id':plan_id,'reminder_id':item['id'],'source_id':item['source_id'],'observed_snapshot_matches_before':observed==before,
                'snapshot_coverage':'reviewed_list_scope_task_membership_parent_group_members_and_account_order','observation_consistency':'separate_live_reads_may_change',
                'original_uncertainty_retained':True,'causal_dispatch_proven':False,'data_mutated':False,'do_not_retry':True,**inspect(engine.store,plan)}
    if item.get('operation') in {'create_group','rename_group'}:
        from .native_reminder_groups import inventory,rename_snapshot
        observed=rename_snapshot(engine.adapter,item) if item['operation']=='rename_group' else inventory(engine.adapter,item['source_id'])
        if engine.context(True)!=context:raise SafetyError('Reminder access changed during group observation')
        from .native_reminder_exchange import inspect
        return {'plan_id':plan_id,'reminder_id':item['id'],'source_id':item['source_id'],'observed_snapshot_matches_before':observed==before,
                'snapshot_coverage':'exact_root_group_inventory_account_order_and_reviewed_child_list_view' if item['operation']=='rename_group' else 'exact_private_root_group_inventory_and_account_order','observation_consistency':'separate_live_reads_may_change',
                'original_uncertainty_retained':True,'causal_dispatch_proven':False,'data_mutated':False,'do_not_retry':True,**inspect(engine.store,plan)}
    lifecycle=item.get('operation') in {'delete_reminder','restore_reminder'}
    if lifecycle:
        from .native_reminder_lifecycle import unchanged_observation
        matches,coverage=unchanged_observation(engine.adapter,item)
        if engine.context(True)!=context:raise SafetyError('Reminder access changed during observation')
        from .native_reminder_exchange import inspect
        return {'plan_id':plan_id,'reminder_id':item['id'],'source_id':item['source_id'],
                'observed_snapshot_matches_before':matches,'snapshot_coverage':coverage,
                'observation_consistency':'separate_live_reads_may_change','original_uncertainty_retained':True,
                'causal_dispatch_proven':False,'data_mutated':False,'do_not_retry':True,**inspect(engine.store,plan)}
    identity, source = item.get('id'), item.get('source_id')
    if (not isinstance(item.get('operation'), str) or item['operation'] not in OBSERVABLE_CHANGES
            or not isinstance(identity, str) or not identity or not isinstance(source, str) or source not in context['source_ids']
            or not isinstance(before, dict) or not isinstance(before.get('item'), dict)
            or before['item'].get('id') != identity or before['item'].get('source_id') != source
            or not isinstance(before.get('list'), dict) or before['list'].get('id') != before['item'].get('list_id')
            or before['list'].get('source_id') != source):
        raise SafetyError('The original exact reminder snapshot is unavailable')
    if item['operation']=='create_subtask':
        import uuid
        child=item.get('child_id')
        try:
            if not isinstance(child,str) or str(uuid.UUID(child)).upper()!=child or child==identity:raise ValueError()
        except ValueError:raise SafetyError('Exact preassigned child identity required') from None
    # This selects the adapter's read-only exact-reminder snapshot path; it does
    # not dispatch or reinterpret the original operation as an update.
    observed = engine.adapter.snapshot(dict(item, operation='update_reminder'))
    if (not isinstance(observed, dict) or not isinstance(observed.get('item'), dict)
            or observed['item'].get('id') != identity or observed['item'].get('source_id') != source
            or not isinstance(observed.get('list'), dict)
            or observed['list'].get('id') != observed['item'].get('list_id')
            or observed['list'].get('source_id') != source):
        raise SafetyError('Observed reminder identity or source differs from the plan')
    matches=observed == before
    coverage='only_fields_exposed_by_the_current_adapter'
    if item['operation']=='move_reminder' and 'source_graph' in before:
        from .native_reminder_moves import observe_unchanged
        matches=observe_unchanged(engine.adapter,item,observed)
        coverage='all_reviewed_subtree_snapshots_and_both_bounded_list_graphs'
    if item['operation']=='reorder_reminder':
        from .native_reminder_order import unchanged
        matches=unchanged(engine.adapter,item,observed)
        coverage='reviewed_root_descendants_anchor_and_complete_manual_sequence'
    candidate=None
    if item['operation']=='create_subtask':
        from .core import NativeObjectNotFound
        child=item['child_id']
        try:
            engine.adapter.reader.read('get',id=child)
        except NativeObjectNotFound:
            recovery=engine.adapter.reader.read('recovery_lookup',source_id=source,id=child)
            if recovery.get('found_in_view') is not False:raise SafetyError('The preassigned child is present or recovery visibility is unavailable')
        else:raise SafetyError('The preassigned child is present; inspect its exact state instead')
        candidate={'id':child,'found_in_selected_live_scope':False,'found_in_recovery_view':False,
                   'coverage':'selected_EventKit_scope_and_provider_recovery_view','absence_globally_proven':False}
        coverage='parent_snapshot_and_preassigned_child_scope_observations'
    if engine.context(True) != context:
        raise SafetyError('Reminder access changed during observation')
    from .native_reminder_exchange import inspect
    terminal=inspect(engine.store,plan)
    result={'plan_id': plan_id, 'reminder_id': identity, 'source_id': source,
            'observed_snapshot_matches_before': matches,
            'snapshot_coverage': coverage,
            'observation_consistency': 'separate_live_reads_may_change',
            'original_uncertainty_retained': True, 'causal_dispatch_proven': False,
            'data_mutated': False, 'do_not_retry': True, **terminal}
    if candidate is not None:result['preassigned_child_observation']=candidate
    return result
