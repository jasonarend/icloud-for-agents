"""Lazy fixed-protocol native reads. No mutation or permission-request actions."""
import json
import os
from pathlib import Path
import platform
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import subprocess
import tempfile

from .core import SafetyError, NativeOperationRefused, digest
from .services import enabled


READ_ACTIONS = {'notes': {'status', 'sources', 'folders', 'folder_get', 'folder_inventory', 'folder_role', 'query', 'get', 'search', 'attachments', 'diagnose_note', 'contains'},
                'reminders': {'status', 'sources', 'lists', 'query', 'get','list_membership'}}
MAX_OUTPUT = 2_000_000


def _confirmed_refusal(name,request,response):
    allowed={'permission_required','not_found_in_scope','invalid_fields','invalid_mutation','invalid_request',
        'unsupported_operation','unsupported_service','notes_validation_timeout','native_dispatch_cancelled','duplicate_folder','duplicate_list','shared_write_not_supported',
        'unsupported_repair_target','unsupported_edit_target','snapshot_changed',
        'snapshot_changed_or_recurrence_unsupported','notes_bridge_unavailable','notes_not_running','notes_model_store_unavailable','notes_model_unavailable','notes_model_has_unrelated_changes'}
    category=response.get('category',response.get('error'))
    reason=('permission_required' if type(response.get('error_code')) is int and response['error_code'] in {-1743,-1744} else
            category if isinstance(category,str) and category in allowed else 'helper_validation')
    guidance=' Check native store availability and macOS app-data access for the signed helper; Notes Automation access is separate.' if name=='notes' and reason=='notes_model_store_unavailable' else ''
    return NativeOperationRefused('Native '+name+' refused before mutation dispatch ('+reason+'); the consumed plan cannot be replayed.'+guidance,
        evidence={'mutation_dispatched':False,'request_sha256':digest(request),'helper_response_sha256':digest(response),'reason':reason})


def native_config(name):
    if name not in READ_ACTIONS or not enabled(name):
        raise SafetyError('Native service is disabled or unknown')
    try:
        config = json.loads(os.environ.get('ICLOUD_NATIVE_CONFIG', '{}'))
    except (ValueError, TypeError):
        raise SafetyError('Invalid native configuration') from None
    try:
        validate_config(config)
    except ValueError:
        raise SafetyError("Invalid native configuration") from None
    return config.get(name, {})


def validate_config(config):
    if not isinstance(config, dict) or set(config) - set(READ_ACTIONS):
        raise ValueError('Unknown native service configuration')
    for name, value in config.items():
        if not isinstance(value, dict) or set(value) - {'source_ids', 'helper_path', 'access_mode','metadata_backend','folder_write_backend'}:
            raise ValueError('Unknown native configuration fields')
        if 'metadata_backend' in value and (name!='notes' or not isinstance(value['metadata_backend'],str) or value['metadata_backend'] not in {'scripting','notes-store-readonly'}):
            raise ValueError('Unknown optional native metadata backend')
        if 'folder_write_backend' in value and (name!='notes' or value['folder_write_backend'] not in ('scripting','notes-shared') or (value['folder_write_backend']=='notes-shared' and value.get('metadata_backend')!='notes-store-readonly')):
            raise ValueError('Native folder writer requires explicit read-only store verification')
        if value.get('access_mode', 'read-only') not in {'disabled', 'read-only', 'write-enabled'}:
            raise ValueError('Invalid native access mode')
        ids = value.get('source_ids', [])
        if (not isinstance(ids, list) or len(ids) > 100 or any(
                not isinstance(s, str) or not 1 <= len(s) <= 1000 or any(ord(c) < 32 for c in s) for s in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Invalid native source IDs')
        helper = value.get('helper_path')
        if helper is not None and (not isinstance(helper, str)
                                   or not Path(helper).is_absolute() or any(ord(c) < 32 for c in helper)):
            raise ValueError('Invalid native helper path')


class NativeReader:
    def __init__(self, name):
        self.name = name

    def read(self, action, **fields):
        config = native_config(self.name)
        global_mode = os.environ.get('ICLOUD_MAIL_ACCESS', 'read-only')
        if global_mode not in {'disabled', 'read-only', 'write-enabled'} or global_mode == 'disabled':
            raise SafetyError('Backend access is disabled or invalid')
        if config.get('access_mode', 'read-only') == 'disabled':
            raise SafetyError('Native service access is disabled')
        if action not in READ_ACTIONS[self.name]:
            raise SafetyError('Unsupported native read action')
        if platform.system() != 'Darwin':
            raise SafetyError('Native Notes and Reminders require macOS')
        sources = config.get('source_ids', [])
        if action not in {'status', 'sources'} and not sources:
            raise SafetyError('Select exact native source IDs before reading content')
        if set(fields) - {'id', 'folder_id', 'list_id', 'limit', 'offset', 'completed', 'text', 'due_date', 'timezone', 'include_overdue', 'due_from', 'due_through', 'priorities'}:
            raise SafetyError('Unknown native read fields')
        limit, offset = fields.get('limit', 50), fields.get('offset', 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise SafetyError('Invalid bounded native page')
        for key in ('id', 'folder_id', 'list_id', 'text'):
            value = fields.get(key)
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 1000):
                raise SafetyError('Invalid native selector')
        if fields.get('completed') is not None and type(fields['completed']) is not bool:
            raise SafetyError('Invalid completion filter')
        dates={key:fields[key] for key in ('due_date','due_from','due_through') if fields.get(key) is not None}
        if dates or fields.get('timezone') is not None:
            try:
                for value in dates.values():
                    if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError()
                ZoneInfo(fields.get('timezone',''))
                if 'due_date' in dates and len(dates)>1:raise ValueError()
                if dates.get('due_from','0001-01-01')>dates.get('due_through','9999-12-31'):raise ValueError()
            except (ValueError,TypeError,ZoneInfoNotFoundError):
                raise SafetyError('Use ordered ISO dates, one daily/range filter and an explicit IANA timezone') from None
        priorities=fields.get('priorities')
        if priorities is not None and (not isinstance(priorities,list) or not 1<=len(priorities)<=4
                or any(type(p) is not int or p not in {0,1,5,9} for p in priorities) or len(set(priorities))!=len(priorities)):
            raise SafetyError('Priorities must select distinct none/high/medium/low values 0/1/5/9')
        if fields.get('include_overdue') is True and 'due_date' not in dates:
            raise SafetyError('Overdue expansion requires an exact due_date')
        if fields.get('include_overdue') is not None and type(fields['include_overdue']) is not bool:
            raise SafetyError('Invalid overdue filter')
        request = {'protocol_version': 1, 'action': action, 'source_ids': sources,
                   **{k: v for k, v in fields.items() if v is not None}}
        if action in {'folders','query','search','attachments','lists'}:
            request.setdefault('limit',limit);request.setdefault('offset',offset)
        if self.name=='notes' and action=='folder_role':
            if config.get('metadata_backend','scripting')!='notes-store-readonly':raise SafetyError('Optional Notes store metadata requires explicit owner configuration; no automatic fallback')
            if set(fields)!={'folder_id'}:raise SafetyError('Folder-role reads require only one exact folder ID')
            from .native_note_store import folder_role
            return folder_role(fields['folder_id'],sources)
        result = self._dispatch(request, config)
        if self.name == 'notes' and action == 'search':
            from .notes_content import project_html
            candidates=result.pop('table_projection_candidates',[])
            if not isinstance(candidates,list) or len(candidates)>limit:
                raise SafetyError('Invalid bounded Notes search projection')
            for candidate in candidates:
                try:
                    projected=project_html(candidate['html'])
                    index=projected['text'].lower().find(fields['text'].lower())
                    if index>=0:
                        result.setdefault('items',[]).append({key:candidate[key] for key in ('id','title','source_id','folder_id')})
                        result['items'][-1].update(excerpt=projected['text'][max(0,index-80):index+240],
                            locked=False,content_complete=False,match_source='exported_table_text',
                            text_projection_complete=projected['text_projection_complete'] and not candidate.get('truncated',False))
                except (ValueError,KeyError,TypeError):
                    result.setdefault('unavailable',[]).append({'category':'table_projection_unavailable'})
            if len(result.get('items',[]))>limit:raise SafetyError('Native search exceeded result limit')
        if self.name == 'notes' and action == 'get' and 'html' in result.get('item', {}):
            from .notes_content import project_html
            try:
                projection = project_html(result['item']['html'])
                result['item']['derived_text'] = projection['text']
                result['item']['native_table_exports'] = projection['tables']
                result['item']['text_projection_complete'] = projection['text_projection_complete'] and not result['item'].get('truncated')
            except ValueError:
                result['item']['text_projection_complete'] = False
                result['item']['projection_error'] = 'unsupported_html_structure'
        return result

    def _dispatch(self, request, config):
        """Internal transport; callers enforce fixed read or approved-write actions."""
        action = request["action"]
        if self.name == 'reminders' or config.get('helper_path'):
            request['service'] = self.name
            helper = config.get('helper_path')
            if not helper:
                raise SafetyError('Native Reminders helper is not configured')
            path = Path(helper)
            if path.is_symlink() or not path.is_file():
                raise SafetyError('Native helper must be a regular executable')
            info = path.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o022 or not os.access(path, os.X_OK):
                raise SafetyError('Native helper must be owner-controlled and executable')
            if self.name == 'notes':
                from .native_app import application_request,bundle_fingerprint
                readonly=action in READ_ACTIONS['notes']
                fingerprint=bundle_fingerprint(path) if readonly else None
                prior_mode=os.environ.get('ICLOUD_MAIL_ACCESS','read-only')
                response = application_request(path, request, timeout=75 if action=="request_access" else 40)
                if readonly and response.get('error')=='notes_operation_failed' and response.get('error_code')==-1712:
                    # Only a completed, fixed read can repeat once. Never retry
                    # mutation, consent, missing response or ambiguous dispatch.
                    if (native_config('notes')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=prior_mode
                            or prior_mode=='disabled' or bundle_fingerprint(path)!=fingerprint):
                        raise SafetyError('Native access or helper changed before the read-only retry')
                    response=application_request(path,request,timeout=40)
                    response=dict(response,read_retry_count=1)
                if 'error' in response:
                    if action=='apply' and response.get('mutation_dispatched') is False:
                        raise _confirmed_refusal(self.name,request,response)
                    if response['error']=='permission_check_timed_out':
                        raise SafetyError('Notes permission check timed out; OS access is unverified. No automatic retry.')
                    if response['error']=='notes_not_running':
                        raise SafetyError('Open Notes before validating native Automation access')
                    if response['error']=='permission_required':
                        raise SafetyError('Notes Automation permission is required for the native helper app')
                    code=response.get('error_code')
                    detail=(' (Apple event code '+str(code)+')') if type(code) is int else ''
                    if response.get('stage') in {'validation','folder_lookup','membership_before','note_constructor','note_insert','membership_after','note_move'}:detail+=' at '+response['stage']
                    raise SafetyError('Native Notes operation failed'+detail+'; no automatic retry')
                return response
            command = [str(path)]
        else:
            if action == 'status':
                return {'protocol_version': 1, 'service': 'notes', 'os_permission': 'not_checked',
                        'reason': 'Configure the native helper for a nonprompting permission check'}
            command = ['/usr/bin/osascript', '-l', 'JavaScript',
                       str(Path(__file__).parent / 'native/notes.js')]
        # Temporary files bound memory and keep potentially private output out of logs.
        env = {k: os.environ[k] for k in ('HOME', 'PATH', 'TMPDIR', 'LANG') if k in os.environ}
        try:
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                encoded = json.dumps(request, ensure_ascii=False).encode()
                if len(encoded) > 65536:
                    raise SafetyError('Native request exceeds input limit')
                process = subprocess.run(command, input=encoded, stdout=output, stderr=errors,
                               timeout=75 if action=="request_access" else 40, check=False, env=env)
                if process.returncode:
                    errors.seek(0)
                    diagnostic = errors.read(8192)
                    if b"(-1743)" in diagnostic or b"(-1744)" in diagnostic:
                        raise SafetyError("Notes Automation permission is required; the user must approve access in macOS Privacy & Security. No automatic retry.")
                    raise SafetyError("Native helper failed; no automatic retry.")
                output.seek(0)
                raw = output.read(MAX_OUTPUT + 1)
            if len(raw) > MAX_OUTPUT:
                raise SafetyError('Native response exceeds limit; narrow the query')
            response = json.loads(raw)
        except SafetyError:
            raise
        except (subprocess.SubprocessError, OSError, ValueError):
            raise SafetyError('Native read failed; inspect OS permission and helper configuration. No automatic retry.') from None
        if not isinstance(response, dict) or response.get('protocol_version') != 1:
            raise SafetyError('Invalid native helper response')
        if 'error' in response:
            if action=='apply' and response.get('mutation_dispatched') is False:
                raise _confirmed_refusal(self.name,request,response)
            code = response['error']
            allowed = {'permission_required', 'not_found_in_scope', 'source_scope_required',
                       'invalid_page', 'response_limit', 'read_failed', 'read_timeout', 'query_limit'}
            if self.name == 'notes' and code == 'permission_required':
                raise SafetyError('Notes Automation permission is required; the user must approve access in macOS Privacy & Security.')
            raise SafetyError('Native read refused: ' + (code if code in allowed else 'helper_error'))
        return response


def register_read_tools(mcp):
    """Register only selected services. Dispatch checks owner settings again."""
    from functools import wraps
    from fastmcp.exceptions import ToolError
    annotations = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': True}

    def guard(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (SafetyError, ValueError) as exc:
                raise ToolError(str(exc)) from None
            except Exception:
                raise ToolError('Native read failed; no automatic retry.') from None
        return wrapped

    if enabled('reminders'):
        @mcp.tool(annotations=annotations)
        @guard
        def reminders_access_status() -> dict:
            """Inspect effective service scope and approval mode without contacting Apple."""
            from .native_access import access_status
            return access_status('reminders')

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_status() -> dict:
            """Inspect existing OS grant without requesting permission or reading content."""
            return NativeReader('reminders').read('status')

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_sources() -> dict:
            """Discover native source identifiers for explicit owner scope selection."""
            return NativeReader('reminders').read('sources')

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_lists(limit: int = 100, offset: int = 0) -> dict:
            """List only the selected native sources' reminder lists."""
            return NativeReader('reminders').read('lists', limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_query(limit: int = 50, offset: int = 0, list_id: str | None = None,
                            completed: bool | None = None, text: str | None = None,
                            due_from: str | None = None, due_through: str | None = None,
                            timezone: str | None = None, priorities: list[int] | None = None) -> dict:
            """Bounded tasks, with optional inclusive due-date range, timezone and priorities. No data changes."""
            return NativeReader('reminders').read('query',limit=limit,offset=offset,list_id=list_id,
                completed=completed,text=text,due_from=due_from,due_through=due_through,timezone=timezone,priorities=priorities)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_daily(date: str, timezone: str, include_overdue: bool = True,
                            limit: int = 50, offset: int = 0) -> dict:
            """Read incomplete reminders due on a date, optionally overdue; no scheduling or delivery."""
            return NativeReader('reminders').read('query', due_date=date, timezone=timezone,
                include_overdue=include_overdue, completed=False, limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_get(id: str) -> dict:
            """Read one exact scoped reminder; advanced native metadata is incomplete."""
            return NativeReader('reminders').read('get', id=id)

    if enabled('notes'):
        @mcp.tool(annotations=annotations)
        @guard
        def notes_folder_role(folder_id:str)->dict:
            """Read exact selected-folder storage role through explicitly opted-in read-only metadata; no note bodies or database writes. Default-folder status requires scripting verification."""
            return NativeReader('notes').read('folder_role',folder_id=folder_id)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_access_status() -> dict:
            """Inspect effective service scope and approval mode without requesting OS access."""
            from .native_access import access_status
            return access_status('notes')

        @mcp.tool(annotations=annotations)
        @guard
        def notes_status() -> dict:
            """Check Notes OS grant through the configured helper without requesting consent."""
            return NativeReader('notes').read('status')

        @mcp.tool(annotations=annotations)
        @guard
        def notes_sources() -> dict:
            """Discover Notes account IDs; OS Automation consent may be requested by macOS."""
            return NativeReader('notes').read('sources')

        @mcp.tool(annotations=annotations)
        @guard
        def notes_folders(limit: int = 100, offset: int = 0) -> dict:
            """Read folder identifiers only within explicitly selected accounts."""
            return NativeReader('notes').read('folders', limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_query(folder_id: str, limit: int = 50, offset: int = 0) -> dict:
            """Read a bounded title page in one exact physical folder; follow next_offset even on empty pages."""
            return NativeReader('notes').read('query', folder_id=folder_id, limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_search(text: str, folder_id: str | None = None, limit: int = 50, offset: int = 0) -> dict:
            """Bounded title, plaintext and exported table-text search. Follow next_offset until done even after empty pages."""
            return NativeReader('notes').read('search', text=text, folder_id=folder_id, limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_attachments(folder_id: str, id: str, limit: int = 50, offset: int = 0) -> dict:
            """Read scoped attachment metadata only; no file download or content changes."""
            return NativeReader('notes').read('attachments', folder_id=folder_id, id=id, limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_get(folder_id: str, id: str) -> dict:
            """Read unlocked note text/HTML with explicit native-content completeness limits."""
            return NativeReader('notes').read('get', folder_id=folder_id, id=id)
