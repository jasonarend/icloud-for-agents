"""Service-specific effective access, shared by native reads and planned writes."""
import os

from .core import SafetyError
from .native_read import READ_ACTIONS, native_config


def access_status(name):
    config = native_config(name)
    global_mode = os.environ.get('ICLOUD_MAIL_ACCESS', 'read-only')
    if global_mode not in {'disabled', 'read-only', 'write-enabled'}:
        raise SafetyError('Invalid runtime access mode')
    mode = config.get('access_mode', 'read-only')
    effective = ('disabled' if 'disabled' in (global_mode, mode) else
                 'write-enabled' if global_mode == mode == 'write-enabled' else 'read-only')
    policy = os.environ.get('ICLOUD_APPROVAL_POLICY', 'local-receipt')
    if policy not in {'local-receipt', 'trusted-client'}:
        raise SafetyError('Invalid approval policy')
    from .native_app import pending_requests
    pending = pending_requests(name)
    # Describe implementation boundaries separately from configuration or OS
    # grants. A listed operation still needs exact target and approval checks.
    unsupported = ({
        'delete_note':'Requires selected read-only folder metadata, a compatible helper and a visible verified system recovery folder.',
        'restore_note':'Requires explicitly selected read-only folder metadata and a compatible helper.',
        'delete_folder':'Complete contents and recoverable deletion effects are unverified.',
        'permanently_delete_note':'No independently verified irreversible deletion route.',
        'move_note':'Requires explicitly selected read-only folder metadata and a compatible helper.',
    } if name=='notes' else {
        'delete_reminder':'Requires the selected ReminderKit backend, compatible helper and a verified root leaf with provider recovery support.',
        'restore_reminder':'Requires the selected ReminderKit backend, compatible helper and a completed owned root-leaf deletion ledger.',
        'delete_list':'Complete contents and recoverable deletion effects are unverified.',
        'permanently_delete_reminder':'No independently verified irreversible deletion route.',
        'move_reminder':'Requires the explicitly selected ReminderKit backend, compatible helper and complete bounded list graphs.',
        'clear_early':'Requires the explicitly selected ReminderKit backend and compatible helper.',
    })
    metadata_moves=name=='notes' and config.get('metadata_backend')=='notes-store-readonly'
    if metadata_moves:unsupported.pop('move_note');unsupported.pop('restore_note');unsupported.pop('delete_note')
    if name=='reminders' and config.get('advanced_backend')=='reminderkit':
        unsupported.pop('clear_early');unsupported.pop('move_reminder');unsupported.pop('delete_reminder');unsupported.pop('restore_reminder')
    return {'pending_native_requests':pending,'service': name, 'effective_access': effective, 'configured_access': mode,
            'runtime_access': global_mode, 'approval_policy': policy,
            'source_ids': config.get('source_ids', []),
            'scope_selected': bool(config.get('source_ids')),
            'os_permission': 'not_checked', 'writes_implemented': True,
            'implemented_write_operations':(['create_note','create_folder','append_note','prepend_note','replace_text','update_note','rename_folder']+(['move_note','restore_note','delete_note'] if metadata_moves else []) if name=='notes' else ['create_list','create_reminder','update_reminder','set_completed','rename_list','set_list_color','set_recurrence']+(['group_list','ungroup_list','rename_group','create_group','delete_reminder','restore_reminder','reorder_reminder','set_list_badge','set_list_pinned','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early','create_subtask','create_section','rename_section','move_reminder'] if config.get('advanced_backend')=='reminderkit' else [])),
            'implemented_recovery_operations':(['accept_observed_creation','repair_title_only_candidate','accept_observed_known_move_state','accept_observed_unchanged_edit'] if name=='notes' else ['known_update_correction']),
            'standing_grants_implemented': True,'one_time_elevation_implemented':True,
            'read_actions': sorted(READ_ACTIONS[name]),
            'unavailable_write_operations':unsupported,
            'operation_listing_is_authorization':False,
            'provider_capabilities':'not_probed'}


def require_write(name,item=None):
    """Refuse before any backend or proposal side effect unless both modes allow writes."""
    status = access_status(name)
    if status['effective_access'] != 'write-enabled':
        from .native_once import permits
        if status['effective_access']=='disabled' or not permits(name,item):
            raise SafetyError('Native service is not separately write-enabled')
    if not status['scope_selected']:
        raise SafetyError('Select exact native source IDs before preparing writes')
    if item is not None and (not isinstance(item,dict) or item.get('source_id') not in status['source_ids']):
        raise SafetyError('Native target source is outside current write scope')
    return status
