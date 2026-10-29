"""Client-neutral exact native creation plans and read-only outcome reconciliation."""
from functools import wraps
import os
from fastmcp.exceptions import ToolError

from .approvals import PlanStore
from .core import SafetyError
from .native_access import access_status,require_write
from .native_changes import NativeChanges
from .native_notes import NotesCreateAdapter
from .native_reminders import RemindersCreateAdapter
from .services import enabled

READ={'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':True}
WRITE={'readOnlyHint':False,'destructiveHint':True,'idempotentHint':False,'openWorldHint':True}


def owner():
    account=os.environ.get('ICLOUD_MAIL_ACCOUNT','')
    if not account or len(account)>254 or any(ord(c)<33 for c in account):
        raise SafetyError('Configure the nonsecret instance owner/account label before native plans')
    return account


def editor(name,write=False):
    if write:require_write(name)
    else:access_status(name)
    adapter=NotesCreateAdapter() if name=='notes' else RemindersCreateAdapter()
    return NativeChanges(name,owner(),adapter,PlanStore())


def check_plan_scope(name,plan_id):
    status=access_status(name)
    plan=PlanStore().read(PlanStore().path(plan_id,'.json'))
    allowed={name+'_change'} | ({'notes_recovery','notes_move_recovery','notes_edit_recovery'} if name=='notes' else {'reminders_observation_recovery'})
    if plan.get('kind') not in allowed or plan.get('payload',{}).get('account')!=owner():
        raise SafetyError('Plan belongs to another service or owner')
    if not set(plan['payload'].get('context',{}).get('source_ids',[]))<=set(status['source_ids']):
        raise SafetyError('Plan lies outside current source access')
    return plan


def guard(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        try:return fn(*args,**kwargs)
        except SafetyError as exc:raise ToolError(str(exc)) from None
        except Exception:raise ToolError('Native proposal/status operation failed. Inspect exact access and outcome; no automatic retry.') from None
    return wrapped


def register_change_tools(mcp):
    def register_permissions(name):
        from .native_permissions import NativePermissions
        def prepare_os_access()->dict:
            """Propose a user-handled OS consent request. Does not enable writes or select sources."""
            return NativePermissions(name,owner()).prepare()
        def request_os_access(plan_id:str)->dict:
            """Execute one approved OS-consent request; the human handles macOS prompts. No data writes."""
            return NativePermissions(name,owner()).execute(plan_id)
        mcp.tool(name=name+'_prepare_os_access',annotations=WRITE)(guard(prepare_os_access))
        mcp.tool(name=name+'_request_os_access',annotations=WRITE)(guard(request_os_access))
        def prepare_once(changes:list[dict])->dict:
            """Propose one exact temporary write batch from read-only access; no persistent elevation. Ordinary approval required."""
            return editor(name).prepare_once(changes)
        mcp.tool(name=name+'_prepare_once',annotations=WRITE)(guard(prepare_once))
        def cancel_plan(plan_id:str)->dict:
            """Cancel an unstarted exact native change plan. Cannot recall an in-flight write."""
            check_plan_scope(name,plan_id)
            return editor(name).cancel(plan_id)
        mcp.tool(name=name+'_cancel_plan',annotations=WRITE)(guard(cancel_plan))
        def finalize_stopped_plan(plan_id:str)->dict:
            """Finalize only a stored proven pre-dispatch native stop; preserve original failure, never retry writes."""
            check_plan_scope(name,plan_id)
            return editor(name).finalize_stopped(plan_id)
        mcp.tool(name=name+'_finalize_stopped_plan',annotations=WRITE)(guard(finalize_stopped_plan))
        from .native_grants import NativeGrants
        def prepare_grant(scope:dict)->dict:
            """Propose ongoing/scenario native permission with exact rules, optional expiry/cap, and revocation. copy_observation is a separate operation for the reviewed copy tools. No data writes."""
            return NativeGrants(editor(name)).prepare(scope)
        def prepare_grant_replacement(grant_id:str,scope:dict)->dict:
            """Review replacement scenario scope; activation retires prior authority and carries quota history forward."""
            return NativeGrants(editor(name)).prepare_replacement(grant_id,scope)
        def activate_grant(grant_id:str)->dict:
            """Activate one reviewed native grant under the existing strict/trusted policy. Does not change OS/config access."""
            return NativeGrants(editor(name)).activate(grant_id)
        def finalize_grant(grant_id:str)->dict:
            """Complete a previously admitted grant activation audit after interruption; no native mutation or new receipt."""
            return NativeGrants(editor(name)).finalize(grant_id)
        def grant_status(grant_id:str)->dict:
            """Read owned grant scope and activation/revocation state; container status is not refreshed."""
            return NativeGrants(editor(name)).status(grant_id)
        def revoke_grant(grant_id:str)->dict:
            """Revoke ongoing native authority; cannot recall an already admitted native effect."""
            return NativeGrants(editor(name)).revoke(grant_id)
        def prepare_with_grant(grant_id:str,changes:list[dict])->dict:
            """Prepare exact changes within one active scenario grant; unsupported operations and wider scope refused."""
            return editor(name).prepare_with_grant(grant_id,changes)
        for suffix,fn in [('prepare_grant',prepare_grant),('prepare_grant_replacement',prepare_grant_replacement),('activate_grant',activate_grant),('grant_status',grant_status),('finalize_grant',finalize_grant),('revoke_grant',revoke_grant),('prepare_with_grant',prepare_with_grant)]:
            mcp.tool(name=name+'_'+suffix,annotations=READ if suffix=='grant_status' else WRITE)(guard(fn))
    for name in ('notes','reminders'):
        if enabled(name):register_permissions(name)

    if enabled('notes'):
        def prepare_note_observation_copy(snapshot:dict,source_id:str,folder_id:str,accept_omissions:bool,grant_id:str|None=None)->dict:
            """Review one lossy copy of an unlocked, nontruncated Notes observation. One-time approval or an explicitly scoped copy_observation grant is required."""
            from .native_note_import import prepare_copy
            return prepare_copy(editor('notes'),snapshot,source_id,folder_id,accept_omissions=accept_omissions,grant_id=grant_id)
        mcp.tool(name='notes_prepare_observation_copy',annotations=WRITE)(guard(prepare_note_observation_copy))
        from .native_move_recovery import NotesMoveRecovery
        from .native_edit_recovery import NotesEditRecovery
        @mcp.tool(annotations=READ)
        @guard
        def notes_inspect_uncertain_edit(plan_id:str, folder_id:str)->dict:
            """Inspect an exact known note after one uncertain edit. This never settles the plan, changes data, or proves dispatch."""
            from .native_edit_observation import inspect_uncertain_note_edit
            check_plan_scope('notes',plan_id)
            return inspect_uncertain_note_edit(editor('notes'),plan_id,folder_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_prepare_edit_recovery(plan_id:str, folder_id:str)->dict:
            """Prepare a reviewed acknowledgement of an exact unchanged note after an uncertain edit; no native data is changed."""
            check_plan_scope('notes',plan_id)
            return NotesEditRecovery(owner()).prepare(plan_id,folder_id)
        @mcp.tool(annotations=WRITE)
        @guard
        def notes_accept_edit_recovery(plan_id:str)->dict:
            """Accept the exact reviewed observation under the selected approval policy; no retry or causal dispatch claim."""
            check_plan_scope('notes',plan_id)
            return NotesEditRecovery(owner()).execute(plan_id)
        @mcp.tool(annotations=WRITE)
        @guard
        def notes_finalize_edit_recovery(plan_id:str)->dict:
            """Finalize an already accepted observation after interruption without native mutation."""
            check_plan_scope('notes',plan_id)
            return NotesEditRecovery(owner()).finalize(plan_id)
        @mcp.tool(annotations=WRITE)
        @guard
        def notes_abandon_edit_recovery(plan_id:str)->dict:
            """Withdraw an observation without clearing the original uncertainty."""
            return NotesEditRecovery(owner()).abandon(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_prepare_move_recovery(plan_id:str)->dict:
            """Review the exact known object's unchanged observable state after one uncertain move. No native mutation or replay."""
            check_plan_scope('notes',plan_id)
            return NotesMoveRecovery(owner()).prepare(plan_id)
        @mcp.tool(annotations=WRITE)
        @guard
        def notes_accept_move_recovery(plan_id:str)->dict:
            """Accept a reviewed known move observation under strict/trusted policy; original dispatch remains unproven."""
            check_plan_scope('notes',plan_id)
            return NotesMoveRecovery(owner()).execute(plan_id)
        @mcp.tool(annotations=WRITE)
        @guard
        def notes_finalize_move_recovery(plan_id:str)->dict:
            """Finalize a successful read-only move-observation audit after interruption; no write replay."""
            check_plan_scope('notes',plan_id)
            return NotesMoveRecovery(owner()).finalize(plan_id)
        @mcp.tool(annotations=WRITE)
        @guard
        def notes_abandon_move_recovery(plan_id:str)->dict:
            """Withdraw an unfinalized observation; retain the original uncertainty and leave native data untouched."""
            return NotesMoveRecovery(owner()).abandon(plan_id)
        @mcp.tool(annotations=WRITE)
        @guard
        def notes_prepare_changes(changes:list[dict])->dict:
            """Prepare <=20 exact Notes changes; ordinary read-only mode refuses.

            Operations: create_note (folder_id, title/body/format), create_folder
            (optional parent_folder_id, title), append_note/prepend_note (id,
            folder_id, body/format), replace_text (id, folder_id, exact find,
            replacement and integer expected_matches), update_note (id, folder_id,
            complete title/body/format), rename_folder (folder_id, title; requires the
            explicitly selected development native writer and store metadata).
            move_note (id, folder_id, destination_folder_id) moves between ordinary
            private folders. restore_note uses those fields to restore from the
            verified system Recently Deleted folder to an ordinary private folder.
            delete_note requires fields.recoverable true and moves an existing
            note into the verified system recovery folder; empty/inaccessible
            recovery folders refuse before dispatch. These operations require
            explicitly selected read-only store metadata.
            All require source_id and fields object.
            Literal replacement preserves the title; prepend follows the title.
            Existing native objects and unsupported body rewrites are refused.
            Use prepare_once or an approved scenario grant for read-only access.
            """
            return editor('notes',write=True).prepare(changes)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_execute_approved(plan_id:str)->dict:
            """Execute one exact Notes plan under the selected strict/trusted policy; never replay."""
            check_plan_scope('notes',plan_id)
            return editor('notes').execute(plan_id)

        @mcp.tool(annotations=READ)
        @guard
        def notes_plan_status(plan_id:str)->dict:
            """Inspect exact scoped plan outcome and reconciliation without contacting Notes."""
            check_plan_scope('notes',plan_id)
            return PlanStore().status(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_reconcile_created(plan_id:str)->dict:
            """Read back known creation IDs and record verified effects; no retry, mutation or approval minting."""
            check_plan_scope('notes',plan_id)
            return editor('notes').reconcile_created(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_prepare_folder_rename_correction(plan_id:str,target:str='before')->dict:
            """Review one returned-ID folder-title correction to before/requested state after failed verification; no automatic replay."""
            check_plan_scope('notes',plan_id)
            return editor('notes').prepare_folder_rename_correction(plan_id,target)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_reconcile_folder_rename_correction(plan_id:str)->dict:
            """Verify a successful exact folder-title correction against scripting and persisted metadata; retain original failure."""
            check_plan_scope('notes',plan_id)
            return editor('notes').reconcile_correction(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_reconcile_known_changes(plan_id:str)->dict:
            """Independently verify complete returned-ID Notes edits or moves after failed readback; no mutation or replay."""
            check_plan_scope('notes',plan_id)
            return editor('notes').reconcile_known_notes(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_prepare_creation_recovery(plan_id:str,candidate_id:str,action:str)->dict:
            """Review one explicit observed-note association or title-only repair after uncertain creation; no replay."""
            from .native_recovery import NotesCreationRecovery
            check_plan_scope('notes',plan_id)
            return NotesCreationRecovery(owner()).prepare(plan_id,candidate_id,action)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_execute_creation_recovery(plan_id:str)->dict:
            """Consume an exact reviewed recovery under strict/trusted policy; preserve the original failed result."""
            from .native_recovery import NotesCreationRecovery
            check_plan_scope('notes',plan_id)
            return NotesCreationRecovery(owner()).execute(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_finalize_creation_recovery(plan_id:str)->dict:
            """Read back a successful recovery to finish an interrupted audit proof; no native mutation or replay."""
            from .native_recovery import NotesCreationRecovery
            check_plan_scope('notes',plan_id)
            return NotesCreationRecovery(owner()).finalize(plan_id)

    if enabled('reminders'):
        def prepare_observation_copy(snapshot:dict,source_id:str,list_id:str,accept_omissions:bool,include_dates:bool=False,include_urls:bool=False,grant_id:str|None=None)->dict:
            """Review up to 10 lossy copies; originals, completion and native metadata are not preserved. Requires one-time approval or an explicitly scoped copy_observation grant."""
            from .native_reminder_import import prepare_copy
            return prepare_copy(editor('reminders'),snapshot,source_id,list_id,accept_omissions=accept_omissions,include_dates=include_dates,include_urls=include_urls,grant_id=grant_id)
        mcp.tool(name='reminders_prepare_observation_copy',annotations=WRITE)(guard(prepare_observation_copy))
        def prepare_ical_copy(calendar_text:str,source_id:str,list_id:str,accept_omissions:bool,include_dates:bool=False,include_urls:bool=False,include_alarms:bool=False,grant_id:str|None=None)->dict:
            """Review a lossy copy from one supported iCalendar VTODO. Absolute display alarms can be copied explicitly; recurrence and other alarms are refused. Copying alarms can schedule notifications."""
            from .native_reminder_ical_import import prepare_ical_copy as prepare
            return prepare(editor('reminders'),calendar_text,source_id,list_id,accept_omissions=accept_omissions,include_dates=include_dates,include_urls=include_urls,include_alarms=include_alarms,grant_id=grant_id)
        mcp.tool(name='reminders_prepare_ical_copy',annotations=WRITE)(guard(prepare_ical_copy))
        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_prepare_changes(changes:list[dict])->dict:
            """Prepare <=20 exact Reminders changes; ordinary read-only refuses.

            create_list uses source_id and fields.title; create_reminder also
            requires list_id. update_reminder/set_completed use
            exact source_id and id. rename_list uses source_id/list_id/title.
            delete_reminder uses exact source_id/id and fields.recoverable=true.
            Only private nonrecurring root leaves without sections or descendants
            are supported; permanent deletion is unavailable. restore_reminder
            requires the same ID/source, fields.deletion_plan_id from this owner's
            completed deletion, optional deletion_index (default 0), and exact
            destination fields.list_id. Restore can reactivate original alarms.
            group_list uses source_id/list_id and fields.group_id for one root
            private physical list into an observed empty private root group.
            ungroup_list uses source_id/list_id, fields.anchor_id and placement
            before/after to return that child to source root at an exact root
            list/group anchor. No tasks are written; their IDs are checked.
            rename_group uses source_id, group_id and fields.title for one
            private root group; observed child lists and account order are preserved.
            create_group uses source_id and fields.title to create one empty
            private root list group, preserving prior account order and all tasks.
            reorder_reminder uses exact source/id, fields.anchor_id and placement
            before/after within one selected private list. Both IDs must be roots.
            Parent/child links and exposed content are preserved; descendants
            count against grants. This observes native manual sequence, not app sorting.
            set_list_badge uses source_id/list_id and fields.badge: kind default,
            or kind emoji/symbol with a value. Native validation requires one
            emoji or an available macOS system symbol. It changes only appearance.
            set_list_pinned uses source_id/list_id and fields.pinned (Boolean),
            with the explicitly selected ReminderKit backend.
            set_list_color uses source_id/list_id and fields.color (#RRGGBB or
            explicit sRGB red/green/blue channels from 0 to 1 with alpha=1).
            Supplied fields for create/update: title, notes, priority (0/1/5/9),
            due/start (explicit date components, optional hour/minute/timezone),
            passive url (or null to clear), recurrence (explicit rule or null at creation),
            alarms (complete supported time/location alarm array; [] clears).
            Time alarms use absolute_date or relative_offset_seconds; relative
            alarms require a timed due date and timezone. Location alarms contain
            location with title, latitude, longitude, radius_meters and proximity
            enter/leave. Review includes exact triggers and notification effects.
            Location-text writes and unsupported advanced alarms are refused.
            add_early uses exact id and fields.unit (minutes, hours, days, weeks, or months) and positive integer fields.count; it adds one early alert to a timed reminder only when the native state is observed empty.
            set_urgent uses exact id and fields.urgent Boolean; it changes current-user urgent notifications.
            set_flagged uses exact id and fields.flagged Boolean, requiring the
            explicitly selected ReminderKit backend. remove_tag uses exact id and
            fields.tag_id for one observed native tag, with the same backend.
            add_tag uses fields.tag_name (letters, digits or underscores).
            replace_tag requires both tag_id and tag_name and creates a new tag identity.
            create_subtask uses the exact parent id and fields.title; it creates a
            native child with a preassigned identity and checks observed siblings.
            rename_section uses source_id, list_id, section_id and fields.title; it preserves the observed section inventory and reminder membership.
            create_section uses source_id, list_id and fields.title; it creates a
            native section with a preassigned identity and checks observed sections
            and list membership. Section order and section membership are unverified.
            A create_subtask grant selects exact parent IDs in destinations; a rename_section grant selects exact section IDs there.
            Ordinary recurring edits are refused. set_recurrence requires exact id
            and fields.recurrence_scope="exact_native_rule" plus fields.recurrence
            (rule or null). Rules require frequency, interval, and end (null, count
            or Unix date); optional native weekday/month/year filters are bounded.
            This changes only the exact uncompleted object's rule configuration;
            occurrence edits, completion generation and historical changes are not
            provided by this operation.
            set_completed uses fields.completed boolean. Reminder moves are paused until
            complete hierarchy effects can be verified. Use prepare_once or an approved scenario grant for
            read-only application access. No operation opens a reference URL.
            """
            return editor('reminders',write=True).prepare(changes)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_execute_approved(plan_id:str)->dict:
            """Execute an exact scoped Reminders plan with owner policy and one-shot admission."""
            check_plan_scope('reminders',plan_id)
            return editor('reminders').execute(plan_id)

        @mcp.tool(annotations=READ)
        @guard
        def reminders_plan_status(plan_id:str)->dict:
            """Inspect scoped durable outcome without credentials or native calls."""
            check_plan_scope('reminders',plan_id)
            return PlanStore().status(plan_id)

        @mcp.tool(annotations=READ)
        @guard
        def reminders_inspect_uncertain_change(plan_id:str)->dict:
            """Read the exact reminder after one uncertain change; this cannot settle it or prove dispatch."""
            from .native_reminder_observation import inspect_uncertain_reminder
            check_plan_scope('reminders',plan_id)
            return inspect_uncertain_reminder(editor('reminders'),plan_id)

        @mcp.tool(annotations=READ)
        @guard
        def reminders_reconcile_lifecycle(plan_id:str)->dict:
            """Independently verify a known native lifecycle state after a retained terminal error. Preserve the original failure and never replay a deletion or restore."""
            from .native_reminder_lifecycle import reconcile
            check_plan_scope('reminders',plan_id)
            return reconcile(editor('reminders'),plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_prepare_observation_recovery(plan_id:str,legacy_missing_terminal:bool=False,accept_unknown_dispatch:bool=False)->dict:
            """Review accepting current scoped observations after a terminal error. accept_unknown_dispatch explicitly accepts a retained terminal error with unknown dispatch, including a preassigned subtask candidate not observed live or in recovery. It never proves global absence or no delayed effects. Separately prepare legacy acceptance when both exchange records are absent. Legacy acceptance requires the owner's separate exact acknowledgement at execution; general write permission does not supply it."""
            from .native_reminder_recovery import ReminderObservationRecovery
            check_plan_scope('reminders',plan_id)
            return ReminderObservationRecovery(owner()).prepare(plan_id,legacy_missing_terminal,accept_unknown_dispatch)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_accept_observation_recovery(plan_id:str,owner_acknowledgement:str|None=None)->dict:
            """Accept the exact reviewed Reminder observation under the configured policy; never replay or change the reminder."""
            from .native_reminder_recovery import ReminderObservationRecovery
            check_plan_scope('reminders',plan_id)
            return ReminderObservationRecovery(owner()).execute(plan_id,owner_acknowledgement)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_finalize_observation_recovery(plan_id:str)->dict:
            """Finalize an already accepted Reminder observation after interruption without native mutation."""
            from .native_reminder_recovery import ReminderObservationRecovery
            check_plan_scope('reminders',plan_id)
            return ReminderObservationRecovery(owner()).finalize(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_abandon_observation_recovery(plan_id:str)->dict:
            """Withdraw a Reminder observation review and retain the original uncertainty."""
            from .native_reminder_recovery import ReminderObservationRecovery
            check_plan_scope('reminders',plan_id)
            return ReminderObservationRecovery(owner()).abandon(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_reconcile_created(plan_id:str)->dict:
            """Verify known created objects after failed readback; no automatic retry or generic uncertainty clear."""
            check_plan_scope('reminders',plan_id)
            return editor('reminders').reconcile_created(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_reconcile_known_changes(plan_id:str)->dict:
            """Independently verify complete returned-ID updates, completion, early alerts, moves or list renames after failed readback; no mutation or replay."""
            check_plan_scope('reminders',plan_id)
            return editor('reminders').reconcile_known_changes(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_prepare_update_correction(plan_id:str,target:str="requested")->dict:
            """Review one exact known-ID reminder or list-color correction after failed readback: requested, or before for explicit restoration."""
            check_plan_scope('reminders',plan_id)
            return editor('reminders').prepare_update_correction(plan_id,target)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_reconcile_correction(plan_id:str)->dict:
            """Verify the chosen requested/before state after successful correction; no mutation, replay or generic uncertainty clear."""
            check_plan_scope('reminders',plan_id)
            return editor('reminders').reconcile_correction(plan_id)
