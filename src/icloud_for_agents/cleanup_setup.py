"""Exact, owner-approved standing Mail cleanup setup without user commands.

Only a launcher-selected installation root is used. The MCP adapter supplies no
path, credentials or approval Boolean. Activation changes the reviewed grant and
its one nonsecret settings field; Mail content is never accessed here.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .approvals import PlanStore
from .cleanup_access import CleanupAccess, validate
from .core import SafetyError, digest
from .local_config import read_settings
from .services import configured, enabled


REQUEST_KEYS = {'account', 'operations', 'source_folders', 'destinations', 'max_batch', 'max_24h', 'days', 'rules_reference'}


def configured_root():
    """Read only the installation root fixed by the validated stdio launcher."""
    value = os.environ.get('ICLOUD_CONFIG_ROOT')
    if (not isinstance(value, str) or not value or not Path(value).is_absolute()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise SafetyError('Standing cleanup setup needs the configured installation launcher')
    return Path(value)


class CleanupSetup:
    def __init__(self, root, account, store=None):
        candidate = Path(root)
        if not candidate.is_absolute() or candidate.is_symlink():
            raise SafetyError('Use the validated installation root')
        self.root = candidate.resolve(strict=True)
        self.account = account
        self.store = store or PlanStore()

    @contextmanager
    def _local(self, locked=False):
        local = self.root / 'local'
        fd = os.open(local, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        lockfd = None
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise SafetyError('Local settings directory must be private and owned')
            if locked:
                lockfd = os.open('cleanup-setup.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                info = os.fstat(lockfd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise SafetyError('Invalid cleanup setup lock')
                fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield fd
        finally:
            if lockfd is not None:
                os.close(lockfd)
            os.close(fd)

    def _settings(self, directory_fd):
        fd = os.open('settings.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise SafetyError('Settings must be a private owned regular file')
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise SafetyError('Settings exceed the bounded setup limit')
        settings = read_settings(self.root)
        if json.loads(raw) != settings:
            raise SafetyError('Settings changed while being inspected')
        return settings, hashlib.sha256(raw).hexdigest()

    def _context(self, directory_fd):
        settings, fingerprint = self._settings(directory_fd)
        self._identity(settings)
        if (not configured(settings)['mail'] or not enabled('mail')
                or settings['access_mode'] != 'write-enabled'
                or os.environ.get('ICLOUD_MAIL_ACCESS') != 'write-enabled'):
            raise SafetyError('Standing cleanup needs separately enabled Mail write access')
        if settings.get('approval_policy', 'local-receipt') != os.environ.get('ICLOUD_APPROVAL_POLICY', 'local-receipt'):
            raise SafetyError('Runtime approval policy differs from owner settings')
        if settings.get('cleanup_grant_id') or os.environ.get('ICLOUD_CLEANUP_GRANT_ID'):
            raise SafetyError('An existing standing cleanup grant must be reviewed separately')
        if settings.get('policy_management_enabled', False) or os.environ.get('ICLOUD_ENABLE_POLICY_MANAGEMENT', '0') == '1':
            raise SafetyError('Initial cleanup setup cannot replace managed policy authority')
        return settings, fingerprint

    def _identity(self, settings):
        if (not isinstance(self.account, str) or not self.account or settings['account'] != self.account
                or os.environ.get('ICLOUD_MAIL_ACCOUNT') != self.account):
            raise SafetyError('Standing cleanup setup belongs to another account')
        expected_root = Path(settings.get('runtime_directory', Path.home() / 'Library' / 'Application Support' / 'icloud-for-agents' / 'runtime'))
        if self.store.root.resolve() != expected_root.resolve():
            raise SafetyError('Standing cleanup proposal store differs from owner settings')

    def _installed(self, directory_fd, grant_id=None):
        settings, _ = self._settings(directory_fd)
        self._identity(settings)
        identity = settings.get('cleanup_grant_id')
        if grant_id is not None and grant_id != identity:
            raise SafetyError('Revocation must name the exact installed cleanup grant')
        if os.environ.get('ICLOUD_CLEANUP_GRANT_ID') not in (None, identity):
            raise SafetyError('Runtime and installed cleanup grant identities differ')
        if identity is None:
            return settings, None, None
        access = CleanupAccess(identity, self.account, root=self.store.root, clock=self.store.clock)
        scope = access.authority.read(access.authority.path(identity, '.json'))
        if not isinstance(scope, dict):
            raise SafetyError('Installed cleanup authority is invalid')
        try:
            validate(scope, self.account, scope.get('issued'))
        except (TypeError, ValueError, OverflowError):
            raise SafetyError('Installed cleanup authority is invalid') from None
        if scope.get('grant_id') != identity:
            raise SafetyError('Installed cleanup authority has a different identity')
        return settings, access, scope

    def status(self):
        """Inspect only this installation's exact standing grant; no credentials."""
        with self._local() as directory_fd:
            settings, access, scope = self._installed(directory_fd)
            if access is None:
                return {'configured': False, 'active': False, 'mail_changed': False}
            revoked_path = access.authority.path(scope['grant_id'], '.revoked')
            revoked = revoked_path.exists()
            if revoked:
                access.authority.read(revoked_path)
            expired = scope['expires'] is not None and self.store.clock() >= scope['expires']
            try:
                access.require_completed_activation(scope)
                activation_verified = True
            except SafetyError:
                activation_verified = False
            return {'configured': True, 'grant_id': scope['grant_id'], 'scope': scope,
                    'active': not revoked and not expired and activation_verified,
                    'revoked': revoked, 'expired': expired, 'activation_verified': activation_verified,
                    'runtime_loaded': os.environ.get('ICLOUD_CLEANUP_GRANT_ID') == settings['cleanup_grant_id'],
                    'mail_changed': False}

    def revoke(self, grant_id):
        """Reduce future authority without removing the fail-closed setting."""
        with self._local(locked=True) as directory_fd:
            _, access, scope = self._installed(directory_fd, grant_id)
            if access is None:
                raise SafetyError('No matching installed cleanup grant exists')
            path = access.authority.path(grant_id, '.revoked')
            if not path.exists():
                access.authority.write_once(path, {'revoked': self.store.clock(), 'scope_sha256': digest(scope)})
            else:
                access.authority.read(path)
            return {'grant_id': grant_id, 'revoked': True, 'mail_changed': False,
                    'effect': 'Future matching cleanup is refused; existing in-flight actions cannot be recalled. The installed grant setting remains fail-closed.'}

    def prepare(self, request):
        """Prepare an exact persistent scope; do not install or enable it."""
        with self._local() as directory_fd:
            _, fingerprint = self._context(directory_fd)
        if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
            raise SafetyError('Provide the complete explicit standing cleanup scope')
        days = request['days']
        if days is not None and (type(days) is not int or not 1 <= days <= 36500):
            raise SafetyError('Choose 1..36500 days or null for no automatic expiry')
        now = self.store.clock()
        plan_id = uuid.uuid4().hex
        scope = {key: value for key, value in request.items() if key != 'days'}
        scope.update(grant_id=uuid.uuid4().hex, activation_plan=plan_id, issued=now,
                     expires=None if days is None else now + days * 86400)
        try:
            validate(scope, self.account, now)
        except (TypeError, ValueError, OverflowError):
            raise SafetyError('Invalid explicit standing cleanup scope') from None
        return self.store.prepare('cleanup_grant', {
            'account': self.account, 'scope': scope, 'settings_sha256': fingerprint,
            'installation_root': str(self.root),
            'effect': 'Install this exact ongoing Mail cleanup scope. No messages change during activation. Future matching batches may archive, mark read or move messages to recoverable Trash.',
            'scope_limits': 'No sending, DAV, purge, starred-message overrides or automatic semantic classification. Other running instances must reload their settings to use the grant.',
        }, plan_id=plan_id)

    def _reviewed(self, plan, directory_fd):
        settings, fingerprint = self._context(directory_fd)
        payload = plan.get('payload', {})
        if (plan.get('kind') != 'cleanup_grant' or payload.get('account') != self.account
                or payload.get('installation_root') != str(self.root)
                or payload.get('settings_sha256') != fingerprint):
            raise SafetyError('Cleanup setup changed since review; prepare a new proposal')
        scope = payload.get('scope')
        try:
            validate(scope, self.account, self.store.clock())
        except (TypeError, ValueError, OverflowError):
            raise SafetyError('Reviewed cleanup scope is invalid') from None
        if scope.get('activation_plan') != plan.get('plan_id'):
            raise SafetyError('Reviewed cleanup scope belongs to another activation')
        access = CleanupAccess(scope['grant_id'], self.account, root=self.store.root, clock=self.store.clock)
        if access.authority.path(scope['grant_id'], '.json').exists() or access.authority.path(scope['grant_id'], '.revoked').exists():
            raise SafetyError('This cleanup grant identity already exists')
        return settings, scope, access

    def _replace_settings(self, directory_fd, settings, expected_sha256, grant_id):
        temporary = 'settings.cleanup-' + uuid.uuid4().hex + '.json'
        changed = dict(settings, cleanup_grant_id=grant_id)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(changed, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            current, fingerprint = self._settings(directory_fd)
            if current != settings or fingerprint != expected_sha256:
                raise SafetyError('Settings changed before cleanup activation')
            os.replace(temporary, 'settings.json', src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        return changed

    def activate(self, plan_id):
        """Consume one exact approval, persist its grant and retain any failure."""
        with self._local(locked=True) as directory_fd:
            candidate = self.store.proposal(plan_id)
            self._reviewed(candidate, directory_fd)
            plan = self.store.consume(plan_id, self.account)
            stage = 'settings_recheck'
            try:
                settings, scope, access = self._reviewed(plan, directory_fd)
                stage = 'grant_write'
                access.authority.write_once(access.authority.path(scope['grant_id'], '.json'), scope)
                stage = 'settings_write'
                changed = self._replace_settings(directory_fd, settings, plan['payload']['settings_sha256'], scope['grant_id'])
                stage = 'readback'
                observed, _ = self._settings(directory_fd)
                # The public scope gate deliberately refuses until the applied
                # result exists. Inspect the staged raw authority here instead.
                raw_scope = access.authority.read(access.authority.path(scope['grant_id'], '.json'))
                validate(raw_scope, self.account, self.store.clock())
                if observed != changed or raw_scope != scope:
                    raise SafetyError('Cleanup activation readback did not match the reviewed scope')
                result = {'status': 'applied', 'grant_id': scope['grant_id'], 'settings_updated': True,
                          'mail_changed': False, 'effective_for_next_batch': True,
                          'activation_plan': plan_id, 'activation_plan_sha256': digest(plan),
                          'scope_sha256': digest(scope),
                          'other_running_instances': 'Reload owner settings before using this grant', 'do_not_retry': True}
            except Exception:
                result = {'status': 'failed_or_uncertain', 'stage': stage, 'mail_changed': False,
                          'do_not_retry': True,
                          'message': 'Inspect the retained grant, settings and original proposal before separately reviewed recovery; do not replay activation.'}
            # Commit the durable terminal result before exposing runtime authority.
            # A crash or failure before this point leaves the installed scope
            # linked to a missing/failed result, so a restarted instance refuses it.
            recorded = self.store.finish(plan_id, result)
            if result['status'] == 'applied':
                os.environ['ICLOUD_CLEANUP_GRANT_ID'] = scope['grant_id']
            return recorded
