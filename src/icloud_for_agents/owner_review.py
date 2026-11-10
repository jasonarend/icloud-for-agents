"""Client-neutral requests for exact, locally reviewed owner receipts.

The client may request a window, but it cannot supply its contents or confirm
it. A local person reviews the stored proposal in the fixed macOS UI. Receipt
creation stays separate from executing a provider mutation.
"""

import fcntl
import json
import math
import os
import stat
import threading
import uuid
from contextlib import contextmanager

from .approvals import PlanStore, authorization_path
from .core import SafetyError, digest
from .services import enabled


def service_for(kind):
    if kind in {'send', 'trash', 'cleanup_grant', 'mail_recovery'}:
        return 'mail'
    if kind == 'calendar_change':
        return 'calendar'
    if kind == 'contact_change':
        return 'contacts'
    if isinstance(kind, str) and kind.startswith('notes_'):
        return 'notes'
    if isinstance(kind, str) and kind.startswith('reminders_'):
        return 'reminders'
    raise SafetyError('Unsupported owner-review proposal')


class OwnerReview:
    """Start one bounded local review without holding a server request open."""

    def __init__(self, store=None, dialog=None):
        self.store = store or PlanStore()
        self.dialog = dialog
        self.lock = threading.Lock()
        self.requests = {}

    def _owned(self, plan):
        payload = plan.get('payload')
        account = os.environ.get('ICLOUD_MAIL_ACCOUNT', '')
        active = True if plan.get('kind') == 'http_client' else enabled(service_for(plan.get('kind')))
        if (not isinstance(payload, dict) or not account or payload.get('account') != account or not active):
            raise SafetyError('Owner review belongs to a disabled service or different account')
        kind = plan.get('kind', '')
        independent = kind == 'http_client' or (isinstance(kind, str) and kind.startswith(('notes_', 'reminders_')))
        if os.environ.get('ICLOUD_MAIL_ACCESS', 'read-only') == 'disabled' and not independent:
            raise SafetyError('Owner review is unavailable while access is disabled')
        return plan

    def _proposal(self, plan_id):
        plan = self._owned(self.store.proposal(plan_id))
        if authorization_path(plan)[1] is not True:
            raise SafetyError('This proposal does not require a local owner receipt')
        return plan

    def _policy_proposal(self, plan_id):
        from .managed_policy import context
        account = os.environ.get('ICLOUD_MAIL_ACCOUNT', '')
        if not account or os.environ.get('ICLOUD_MAIL_ACCESS', 'read-only') == 'disabled':
            raise SafetyError('Managed-policy owner review is unavailable')
        manager, _, _ = context(account)
        if manager.approval_policy != 'local-receipt':
            raise SafetyError('Managed policy does not require a local owner receipt')
        plan = manager.store.read(manager.store.path(plan_id, '.json'))
        if (plan.get('plan_id') != plan_id or plan.get('kind') not in {'policy-change', 'one-time-trash'}
                or type(plan.get('expires')) not in (int, float) or not math.isfinite(plan['expires'])
                or manager.clock() >= plan['expires'] or manager.store.path(plan_id, '.started').exists()
                or manager.store.path(plan_id, '.revoked').exists()):
            raise SafetyError('Managed-policy proposal expired, started or revoked')
        current = manager.current()
        payload = plan.get('payload', {})
        if (not isinstance(payload, dict) or
                (plan['kind'] == 'policy-change' and payload.get('before') != current) or
                (plan['kind'] == 'one-time-trash' and
                 (payload.get('policy_revision') != current['revision'] or payload.get('policy_sha256') != digest(current)))):
            raise SafetyError('Managed policy changed since the proposal was prepared')
        return manager, plan

    def _resolve(self, plan_id, scope):
        if scope == 'service':
            return self.store, self._proposal(plan_id)
        if scope == 'managed-policy':
            manager, plan = self._policy_proposal(plan_id)
            return manager.store, plan
        raise SafetyError('Unknown owner-review scope')

    @contextmanager
    def _receipt_lock(self, store, scope):
        """Do not report a receipt while its writer still holds the admission lock."""
        store.ready()
        name = 'management.lock' if scope == 'managed-policy' else 'approval-policy.lock'
        fd = os.open(store.root / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise SafetyError('Invalid owner-review admission lock')
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def request(self, plan_id, scope='service'):
        store, plan = self._resolve(plan_id, scope)
        fingerprint = digest(plan)
        key = (scope, plan_id)
        with self.lock:
            previous = self.requests.get(key)
            if previous and previous['status'] == 'pending':
                return {'plan_id': plan_id, 'request_id': previous['request_id'], 'status': 'pending'}
        try:
            with self._receipt_lock(store, scope):
                if store.path(plan_id, '.approved').exists():
                    receipt = store.read(store.path(plan_id, '.approved'))
                    if receipt.get('plan_id') != plan_id or receipt.get('sha256') != fingerprint:
                        raise SafetyError('Existing owner receipt does not match the proposal')
                    return {'plan_id': plan_id, 'status': 'approved'}
                with self.lock:
                    previous = self.requests.get(key)
                    if previous and previous['status'] == 'pending':
                        return {'plan_id': plan_id, 'request_id': previous['request_id'], 'status': 'pending'}
                    if any(item['status'] == 'pending' for item in self.requests.values()):
                        raise SafetyError('Another local owner review is already open')
                    request = {'request_id': uuid.uuid4().hex, 'status': 'pending'}
                    self.requests[key] = request
        except BlockingIOError:
            return {'plan_id': plan_id, 'status': 'pending'}
        # The complete escaped proposal is rendered as plain text; caller-supplied
        # UI text, scripts, commands and paths are never accepted.
        review = json.dumps(plan, ensure_ascii=True, indent=2)
        review += '\n\nProposal SHA256: ' + fingerprint
        thread = threading.Thread(target=self._review, args=(plan_id, scope, request['request_id'], fingerprint, review), daemon=True)
        thread.start()
        return {'plan_id': plan_id, 'request_id': request['request_id'], 'status': 'pending'}

    def _set_status(self, plan_id, scope, request_id, status):
        with self.lock:
            item = self.requests.get((scope, plan_id))
            if item and item['request_id'] == request_id:
                item['status'] = status

    def _review(self, plan_id, scope, request_id, fingerprint, review):
        try:
            if self.dialog is None:
                from .owner_dialog import request_owner_decision
                decision = request_owner_decision(review, 'Review this exact iCloud for Agents proposal')
            else:
                decision = self.dialog(review, 'Review this exact iCloud for Agents proposal')
            if decision not in ('approved', 'declined'):
                raise SafetyError('Owner review returned an invalid decision')
            if decision == 'declined':
                self._set_status(plan_id, scope, request_id, 'declined')
                return
            if scope == 'managed-policy':
                manager, _ = self._policy_proposal(plan_id)
                with manager.locked():
                    current_manager, current = self._policy_proposal(plan_id)
                    if current_manager.store.root != manager.store.root or digest(current) != fingerprint:
                        raise SafetyError('Managed-policy proposal changed during local review')
                    manager.store.write_once(manager.store.path(plan_id, '.approved'), {
                        'plan_id': plan_id, 'sha256': fingerprint, 'time': manager.clock(),
                        'authorization': 'local-owner-graphical-review',
                    })
                self._set_status(plan_id, scope, request_id, 'approved')
                return
            self.store.ready()
            fd = os.open(self.store.root / 'approval-policy.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise SafetyError('Invalid approval policy lock')
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                current = self._proposal(plan_id)
                if digest(current) != fingerprint:
                    raise SafetyError('Proposal changed during local review')
                self.store.write_once(self.store.path(plan_id, '.approved'), {
                    'plan_id': plan_id, 'sha256': fingerprint, 'approved_at': self.store.clock(),
                    'authorization': 'local-owner-graphical-review',
                })
            finally:
                os.close(fd)
            self._set_status(plan_id, scope, request_id, 'approved')
        except Exception:
            # Never expose proposal content, process stderr or authorization data.
            self._set_status(plan_id, scope, request_id, 'not_approved')

    def status(self, plan_id, scope='service'):
        if scope == 'managed-policy':
            from .managed_policy import context
            if os.environ.get('ICLOUD_MAIL_ACCESS', 'read-only') == 'disabled':
                raise SafetyError('Managed-policy owner review is unavailable')
            manager, _, _ = context(os.environ.get('ICLOUD_MAIL_ACCOUNT', ''))
            plan = manager.store.read(manager.store.path(plan_id, '.json'))
            if plan.get('plan_id') != plan_id or plan.get('kind') not in {'policy-change', 'one-time-trash'}:
                raise SafetyError('Invalid managed-policy review')
            if manager.store.path(plan_id, '.started').exists():
                return {'plan_id': plan_id, 'status': 'started_or_uncertain'}
            if manager.store.path(plan_id, '.revoked').exists():
                return {'plan_id': plan_id, 'status': 'revoked'}
            if type(plan.get('expires')) not in (int, float) or not math.isfinite(plan['expires']) or manager.clock() >= plan['expires']:
                return {'plan_id': plan_id, 'status': 'expired'}
            with self.lock:
                item = self.requests.get((scope, plan_id))
                if item and item['status'] == 'pending':
                    return {'plan_id': plan_id, 'request_id': item['request_id'], 'status': 'pending'}
            if manager.store.path(plan_id, '.approved').exists():
                try:
                    with self._receipt_lock(manager.store, scope):
                        receipt = manager.store.read(manager.store.path(plan_id, '.approved'))
                except BlockingIOError:
                    return {'plan_id': plan_id, 'status': 'pending'}
                if receipt.get('plan_id') != plan_id or receipt.get('sha256') != digest(plan):
                    raise SafetyError('Managed-policy receipt does not match the proposal')
                return {'plan_id': plan_id, 'status': 'approved'}
            with self.lock:
                item = self.requests.get((scope, plan_id))
                if item:
                    return {'plan_id': plan_id, 'request_id': item['request_id'], 'status': item['status']}
            return {'plan_id': plan_id, 'status': 'awaiting_local_approval'}
        if scope != 'service':
            raise SafetyError('Unknown owner-review scope')
        # Validate the ID before consulting process-local state.
        plan = self.store.read(self.store.path(plan_id, '.json'))
        if plan.get('plan_id') != plan_id:
            raise SafetyError('Invalid owner-review proposal')
        self._owned(plan)
        if (not self.store.path(plan_id, '.started').exists() and not self.store.path(plan_id, '.result').exists()
                and not self.store.path(plan_id, '.cancelled').exists()
                and (type(plan.get('expires')) not in (int, float) or not math.isfinite(plan['expires'])
                     or self.store.clock() > plan['expires'])):
            return {'plan_id': plan_id, 'status': 'expired'}
        terminal = any(self.store.path(plan_id, suffix).exists() for suffix in ('.started', '.result', '.cancelled'))
        if not terminal:
            with self.lock:
                item = self.requests.get((scope, plan_id))
                if item and item['status'] == 'pending':
                    return {'plan_id': plan_id, 'request_id': item['request_id'], 'status': 'pending'}
        try:
            if not terminal:
                with self._receipt_lock(self.store, scope):
                    durable = self.store.status(plan_id)
            else:
                durable = self.store.status(plan_id)
        except BlockingIOError:
            return {'plan_id': plan_id, 'status': 'pending'}
        if durable.get('status') != 'awaiting_local_approval':
            return {'plan_id': plan_id, 'status': durable['status']}
        with self.lock:
            item = self.requests.get((scope, plan_id))
            if item:
                return {'plan_id': plan_id, 'request_id': item['request_id'], 'status': item['status']}
        return {'plan_id': plan_id, 'status': 'awaiting_local_approval'}
