"""Service-specific effective access, shared by native reads and planned writes."""
import os

from .core import SafetyError
from .native_read import READ_ACTIONS, native_config


# Implementation inventory, never an OS/provider capability probe or grant.
UNAVAILABLE_FEATURES = {
    'notes': {
        'folder_reparenting': 'No verified safe writer; a synthetic empty-folder move marked its source deleted after returning an error.',
        'folder_deletion': 'Complete contents and recoverable subtree effects are unverified.',
        'native_checklist_writes': 'Saved checklist reads are available; native creation, toggle, reorder and deletion have no verified writer.',
        'pin_tag_smart_folder_writes': 'Native observations are available; mutation routes remain unverified or locally unavailable.',
        'rich_object_writes': 'Native table-cell, drawing/ink, annotation, highlight and collapsed-section editing are unavailable.',
        'attachment_writes': 'No verified native insertion/removal route; scoped file exports and derived PNG/JPEG OCR are separate reads.',
        'native_ocr_handwriting': 'Derived printed-image OCR is available; native Notes OCR/index and handwriting provenance are not reconstructed.',
        'native_recording_transcripts': 'Supplied transcript text can be saved; native recording/transcript retrieval and microphone capture are unavailable.',
        'math_notes': 'Native expression evaluation and object mutation have no verified route.',
        'collaboration': 'Participant permissions, mentions/activity, invitations and shared-content writes are unverified.',
        'password_operations': 'No password collection, lock-setting changes or programmatic unlock; the user handles unlocking in Notes.',
        'permanent_deletion': 'No purge or empty-Trash operation.',
    },
    'reminders': {
        'container_deletion': 'Complete list/group contents and recoverable deletion effects are unverified.',
        'section_order_and_deletion': 'Bounded create/rename/leaf membership moves are available; ordering save failed locally and deletion is unverified.',
        'deeper_parent_writes': 'Verified root/direct-child operations do not authorize deeper parent or subtree sibling ordering.',
        'broader_group_moves': 'Nested groups and custom Smart List moves have no verified writer.',
        'template_writes': 'Saved template reads, bounded name-only renaming and bounded plain-source creation are available with their required optional backends; content editing, deletion, instantiation and sharing changes are unverified.',
        'arbitrary_smart_filters': 'Only the documented Lists-only AND creation/rename/source changes are supported; other criteria and deletion are unavailable.',
        'grocery_writes': 'Empty private list categorization toggles and bounded private custom category creation are available with the selected ReminderKit helper. Exact category assignment additionally requires the read-only store guard. Populated conversions, shopping sessions, classifier behavior and predefined-category configuration remain unverified.',
        'collaboration': 'Assignment/participant identity, assign/unassign, invitations and shared-list writes are unverified.',
        'attachment_writes': 'Bounded native observations/exports are readable; image/file/rich-preview insertion and removal are unavailable.',
        'person_vehicle_triggers': 'No complete verified native representation and writer.',
        'apple_intelligence': 'No verified suggestion or Apple Intelligence automation route.',
        'permanent_deletion': 'No purge or empty-Trash operation.',
    },
}


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
        'add_image':'Native scripting did not provide a verified single-image append; one tested form produced duplicate references. File export is separately supported.',
        'delete_note':'Requires selected read-only folder metadata, a compatible helper and a visible verified system recovery folder.',
        'restore_note':'Requires explicitly selected read-only folder metadata and a compatible helper.',
        'delete_folder':'Complete contents and recoverable deletion effects are unverified.',
        'permanently_delete_note':'No independently verified irreversible deletion route.',
        'move_note':'Requires explicitly selected read-only folder metadata and a compatible helper.',
    } if name=='notes' else {
        'delete_reminder':'Requires selected ReminderKit, a compatible helper and verified provider recovery. Defaults to a root leaf; explicit include_descendants can review a bounded root/direct-child family.',
        'restore_reminder':'Requires selected ReminderKit, a compatible helper and the completed owned deletion ledger; a family also needs explicit include_descendants and its original list.',
        'delete_list':'Complete contents and recoverable deletion effects are unverified.',
        'permanently_delete_reminder':'No independently verified irreversible deletion route.',
        'move_reminder':'Requires the explicitly selected ReminderKit backend, compatible helper and complete bounded list graphs.',
        'create_template':'Requires explicit read-only store content verification, selected ReminderKit and a bounded private plain source list.',
        'rename_template':'Requires the selected ReminderKit backend, explicit read-only Reminders store selection, updated helper and an unlinked bounded saved template.',
        'clear_early':'Requires the explicitly selected ReminderKit backend and compatible helper.',
    })
    metadata_moves=name=='notes' and config.get('metadata_backend')=='notes-store-readonly'
    if metadata_moves:unsupported.pop('move_note');unsupported.pop('restore_note');unsupported.pop('delete_note')
    if name=='reminders' and config.get('advanced_backend')=='reminderkit':
        unsupported.pop('clear_early');unsupported.pop('move_reminder');unsupported.pop('delete_reminder');unsupported.pop('restore_reminder')
        if config.get('metadata_backend')=='reminders-store-readonly':unsupported.pop('rename_template');unsupported.pop('create_template')
    return {'pending_native_requests':pending,'service': name, 'effective_access': effective, 'configured_access': mode,
            'runtime_access': global_mode, 'approval_policy': policy,
            'metadata_backend':config.get('metadata_backend','scripting' if name=='notes' else 'native'),
            'source_ids': config.get('source_ids', []),
            'scope_selected': bool(config.get('source_ids')),
            'os_permission': 'not_checked', 'writes_implemented': True,
            'implemented_write_operations':(['create_note','create_folder','append_note','prepend_note','replace_text','update_note','rename_folder']+(['move_note','restore_note','delete_note'] if metadata_moves else []) if name=='notes' else ['create_list','create_reminder','update_reminder','set_completed','rename_list','set_list_color','set_recurrence']+((['set_grocery_category','set_section','rename_template','create_template'] if config.get('metadata_backend')=='reminders-store-readonly' else [])+['create_grocery_category','set_grocery_categorization','create_smart_list','set_smart_list_sources','rename_smart_list','reparent_reminder','reorder_group','group_list','ungroup_list','rename_group','create_group','delete_reminder','restore_reminder','reorder_reminder','set_list_badge','set_list_pinned','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early','create_subtask','create_section','rename_section','move_reminder'] if config.get('advanced_backend')=='reminderkit' else [])),
            'implemented_recovery_operations':(['accept_observed_creation','repair_title_only_candidate','accept_observed_known_move_state','accept_observed_unchanged_edit'] if name=='notes' else ['known_update_correction']),
            'standing_grants_implemented': True,'one_time_elevation_implemented':True,
            'read_actions': sorted(READ_ACTIONS[name]),
            'unavailable_write_operations':unsupported,
            'unavailable_feature_families':dict(UNAVAILABLE_FEATURES[name]),
            'implementation_inventory_scope':'Documented bounded subsets only; configuration does not prove helper, OS, provider, object or client compatibility.',
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
