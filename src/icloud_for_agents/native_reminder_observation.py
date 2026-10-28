"""Read-only observation of one pre-existing reminder after an uncertain change.

An observation is not evidence of whether the original request was dispatched,
and cannot settle or replay it. No title, notes or other contents are returned.
"""
from .core import SafetyError, digest

OBSERVABLE_CHANGES = {'update_reminder', 'set_completed', 'set_recurrence', 'set_flagged',
                      'remove_tag', 'add_tag', 'replace_tag', 'set_urgent', 'add_early',
                      'clear_early', 'move_reminder'}


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
    identity, source = item.get('id'), item.get('source_id')
    if (not isinstance(item.get('operation'), str) or item['operation'] not in OBSERVABLE_CHANGES
            or not isinstance(identity, str) or not identity or not isinstance(source, str) or source not in context['source_ids']
            or not isinstance(before, dict) or not isinstance(before.get('item'), dict)
            or before['item'].get('id') != identity or before['item'].get('source_id') != source
            or not isinstance(before.get('list'), dict) or before['list'].get('id') != before['item'].get('list_id')
            or before['list'].get('source_id') != source):
        raise SafetyError('The original exact reminder snapshot is unavailable')
    # This selects the adapter's read-only exact-reminder snapshot path; it does
    # not dispatch or reinterpret the original operation as an update.
    observed = engine.adapter.snapshot(dict(item, operation='update_reminder'))
    if (not isinstance(observed, dict) or not isinstance(observed.get('item'), dict)
            or observed['item'].get('id') != identity or observed['item'].get('source_id') != source
            or not isinstance(observed.get('list'), dict)
            or observed['list'].get('id') != observed['item'].get('list_id')
            or observed['list'].get('source_id') != source):
        raise SafetyError('Observed reminder identity or source differs from the plan')
    if engine.context(True) != context:
        raise SafetyError('Reminder access changed during observation')
    from .native_reminder_exchange import inspect
    terminal=inspect(engine.store,plan)
    return {'plan_id': plan_id, 'reminder_id': identity, 'source_id': source,
            'observed_snapshot_matches_before': observed == before,
            'snapshot_coverage': 'only_fields_exposed_by_the_current_adapter',
            'observation_consistency': 'separate_live_reads_may_change',
            'original_uncertainty_retained': True, 'causal_dispatch_proven': False,
            'data_mutated': False, 'do_not_retry': True, **terminal}
