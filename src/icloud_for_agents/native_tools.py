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
    allowed={name+'_change'} | ({'notes_recovery'} if name=='notes' else set())
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
    for name in ('notes','reminders'):
        if enabled(name):register_permissions(name)

    if enabled('notes'):
        @mcp.tool(annotations=WRITE)
        @guard
        def notes_prepare_changes(changes:list[dict])->dict:
            """Prepare <=20 exact create_note/create_folder changes; no writes during preparation."""
            return editor('notes',write=True).prepare(changes)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_execute_approved(plan_id:str)->dict:
            """Execute one exact Notes plan under the selected strict/trusted policy; never replay."""
            require_write('notes');check_plan_scope('notes',plan_id)
            return editor('notes',write=True).execute(plan_id)

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
        def notes_prepare_creation_recovery(plan_id:str,candidate_id:str,action:str)->dict:
            """Review one explicit observed-note association or title-only repair after uncertain creation; no replay."""
            from .native_recovery import NotesCreationRecovery
            require_write('notes');check_plan_scope('notes',plan_id)
            return NotesCreationRecovery(owner()).prepare(plan_id,candidate_id,action)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_execute_creation_recovery(plan_id:str)->dict:
            """Consume an exact reviewed recovery under strict/trusted policy; preserve the original failed result."""
            from .native_recovery import NotesCreationRecovery
            require_write('notes');check_plan_scope('notes',plan_id)
            return NotesCreationRecovery(owner()).execute(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def notes_finalize_creation_recovery(plan_id:str)->dict:
            """Read back a successful recovery to finish an interrupted audit proof; no native mutation or replay."""
            from .native_recovery import NotesCreationRecovery
            require_write('notes');check_plan_scope('notes',plan_id)
            return NotesCreationRecovery(owner()).finalize(plan_id)

    if enabled('reminders'):
        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_prepare_changes(changes:list[dict])->dict:
            """Prepare <=20 exact creates, field updates, completion changes or same-source moves; read-only refuses."""
            return editor('reminders',write=True).prepare(changes)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_execute_approved(plan_id:str)->dict:
            """Execute an exact scoped Reminders plan with owner policy and one-shot admission."""
            require_write('reminders');check_plan_scope('reminders',plan_id)
            return editor('reminders',write=True).execute(plan_id)

        @mcp.tool(annotations=READ)
        @guard
        def reminders_plan_status(plan_id:str)->dict:
            """Inspect scoped durable outcome without credentials or native calls."""
            check_plan_scope('reminders',plan_id)
            return PlanStore().status(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_reconcile_created(plan_id:str)->dict:
            """Verify known created objects after failed readback; no automatic retry or generic uncertainty clear."""
            check_plan_scope('reminders',plan_id)
            return editor('reminders').reconcile_created(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_prepare_update_correction(plan_id:str)->dict:
            """Propose one exact correction of a known-ID update after failed readback; ordinary approval required."""
            require_write('reminders');check_plan_scope('reminders',plan_id)
            return editor('reminders',write=True).prepare_update_correction(plan_id)

        @mcp.tool(annotations=WRITE)
        @guard
        def reminders_reconcile_correction(plan_id:str)->dict:
            """Verify the successful correction and original requested state; never replay or clear unknown effects."""
            require_write('reminders');check_plan_scope('reminders',plan_id)
            return editor('reminders',write=True).reconcile_correction(plan_id)
