"""Lazy fixed-protocol native reads. No mutation or permission-request actions."""
import json
import os
from pathlib import Path
import platform
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import subprocess
import tempfile

from .core import SafetyError
from .services import enabled


READ_ACTIONS = {'notes': {'status', 'sources', 'folders', 'folder_get', 'query', 'get'},
                'reminders': {'status', 'sources', 'lists', 'query', 'get'}}
MAX_OUTPUT = 2_000_000


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
        if not isinstance(value, dict) or set(value) - {'source_ids', 'helper_path', 'access_mode'}:
            raise ValueError('Unknown native configuration fields')
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
        if set(fields) - {'id', 'folder_id', 'list_id', 'limit', 'offset', 'completed', 'text', 'due_date', 'timezone', 'include_overdue'}:
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
        if fields.get('due_date') is not None:
            try:
                due = fields['due_date']
                if not isinstance(due, str) or date.fromisoformat(due).isoformat() != due:
                    raise ValueError()
                ZoneInfo(fields.get('timezone', ''))
            except (ValueError, TypeError, ZoneInfoNotFoundError):
                raise SafetyError('Use an ISO date and an explicit IANA timezone') from None
        if fields.get('include_overdue') is not None and type(fields['include_overdue']) is not bool:
            raise SafetyError('Invalid overdue filter')
        request = {'protocol_version': 1, 'action': action, 'source_ids': sources,
                   **{k: v for k, v in fields.items() if v is not None}}
        return self._dispatch(request, config)

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
                               timeout=25, check=False, env=env)
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
                            completed: bool | None = None, text: str | None = None) -> dict:
            """Bounded live page; offsets may shift during external edits. No mutations."""
            return NativeReader('reminders').read('query', limit=limit, offset=offset,
                                                  list_id=list_id, completed=completed, text=text)

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
            """Read a bounded note-title page in one exact folder; no body search yet."""
            return NativeReader('notes').read('query', folder_id=folder_id, limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_get(folder_id: str, id: str) -> dict:
            """Read unlocked note text/HTML with explicit native-content completeness limits."""
            return NativeReader('notes').read('get', folder_id=folder_id, id=id)
