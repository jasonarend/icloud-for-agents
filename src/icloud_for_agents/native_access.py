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
    return {'pending_native_requests':pending,'service': name, 'effective_access': effective, 'configured_access': mode,
            'runtime_access': global_mode, 'approval_policy': policy,
            'source_ids': config.get('source_ids', []),
            'scope_selected': bool(config.get('source_ids')),
            'os_permission': 'not_checked', 'writes_implemented': True,
            'implemented_write_operations':(['create_note','create_folder','append_note','update_note','move_note'] if name=='notes' else ['create_list','create_reminder','update_reminder','set_completed','move_reminder']),
            'implemented_recovery_operations':(['accept_observed_creation','repair_title_only_candidate'] if name=='notes' else ['known_update_correction']),
            'standing_grants_implemented': True,'one_time_elevation_implemented':True,
            'read_actions': sorted(READ_ACTIONS[name])}


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
