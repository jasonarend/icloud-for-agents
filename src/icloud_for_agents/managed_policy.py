"""Opt-in production policy adapter. No default registration or activation.

Keeps the existing base grant revocable. No credential or provider access is added.
Exact exception approval is not an automatic fallback after a standing refusal.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import time
from . import core
from .approvals import PlanStore, runtime_root
from .cleanup_access import CleanupAccess
from .policy_management import PolicyManager
from .services import enabled


def feature_enabled():
    value = os.environ.get('ICLOUD_ENABLE_POLICY_MANAGEMENT', '0')
    if value not in {'0', '1'}:
        raise core.SafetyError('Invalid managed-policy switch')
    return value == '1'


def context(account):
    if not feature_enabled() or not enabled('mail') or os.environ.get('ICLOUD_MAIL_ACCOUNT') != account:
        raise core.SafetyError('Managed policy is disabled or account/service mismatched')
    grant = os.environ.get('ICLOUD_CLEANUP_GRANT_ID')
    if not grant:
        raise core.SafetyError('An existing owner-approved base cleanup grant is required')
    access = CleanupAccess(grant, account)
    base = access.scope()  # base revocation and lifetime remain mandatory
    manager = PolicyManager(runtime_root() / 'managed-policy',
                            approval_policy=os.environ.get('ICLOUD_APPROVAL_POLICY', 'local-receipt'))
    binding = manager.store.read(manager.store.root / 'binding.json')
    if binding != {'account':account, 'base_grant_id':grant} or manager.current()['policy']['account'] != account:
        raise core.SafetyError('Managed policy binding differs from the installed base grant')
    return manager, access, base


def initialize_from_base(account, grant_id):
    """Local setup after explicit activation approval; never an MCP tool.

    Creates only the separate policy workspace. Does not enable the feature or
    change private settings, permissions, credentials, grants or service state.
    """
    PlanStore().ready()
    access = CleanupAccess(grant_id, account)
    scope = access.scope()
    manager = PolicyManager(runtime_root() / 'managed-policy')
    policy = {key:scope[key] for key in ('account','operations','source_folders','destinations','max_batch','max_24h','expires')}
    policy['protect_starred'] = True
    manager.initialize_offline(policy)
    manager.store.write_once(manager.store.root / 'binding.json', {'account':account,'base_grant_id':grant_id})
    return {'initialized':True, 'activated':False, 'policy_sha256':core.digest(manager.current())}


def assert_settled(manager):
    for started in manager.store.root.glob('*.started'):
        result = started.with_suffix('.result')
        if not result.exists() or manager.store.read(result).get('status') != 'completed':
            raise core.SafetyError('Managed action unfinished or uncertain; reconcile before another write')


def assert_settled_context(account):
    manager, _, _ = context(account)
    assert_settled(manager)


class ManagedCleanupAccess(CleanupAccess):
    """Ordinary cleanup uses the reviewed managed revision; stars stay protected."""
    def scope(self):
        manager, _, base = context(self.account)
        assert_settled(manager)
        policy = manager.current()['policy']
        result = dict(base)
        for key in ('operations','source_folders','destinations','max_batch','max_24h','expires'):
            result[key] = policy[key]
        # The base authority can never be extended beyond its original lifetime.
        if base['expires'] is not None and (result['expires'] is None or result['expires'] > base['expires']):
            result['expires'] = base['expires']
        from .cleanup_access import validate
        return validate(result, self.account, self.clock())


@contextmanager
def ledger_guard(access):
    access.audit.ready()
    fd = os.open(access.audit.root / 'ledger.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise core.SafetyError('Invalid cleanup ledger lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        access.scope()
        entries = []
        for path in access.audit.root.glob('*.started'):
            entry = access.audit.read(path)
            if entry.get('account') != access.account:
                continue
            result = path.with_suffix('.result')
            if not result.exists() or access.audit.read(result).get('status') != 'completed':
                raise core.SafetyError('Uncertain cleanup outcome blocks policy execution')
            entries.append(entry)
        yield entries
    finally:
        os.close(fd)


@contextmanager
def mail_guard(store, account):
    store.ready()
    fd = os.open(store.root / 'approval-policy.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise core.SafetyError('Invalid mail admission lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for path in store.root.glob('*.started'):
            previous = store.read(store.path(path.stem, '.json'))
            if previous['payload'].get('account') != account:
                continue
            result = path.with_suffix('.result')
            if not result.exists() or store.read(result).get('status') not in {'applied','moved','sent','completed'}:
                if not store._reconciliation_completed(path.stem):
                    raise core.SafetyError('Prior mail/DAV outcome uncertain; reconcile before managed execution')
        yield
    finally:
        os.close(fd)


def prepare_exception(service, mail_plan_id, deviations):
    manager, _, _ = context(service.account)
    plan = service.store.proposal(mail_plan_id)
    payload = plan['payload']
    with service.imap() as client:
        if core.role_folder(client, 'Trash', service.overrides) != payload.get('destination'):
            raise core.SafetyError('Exact Trash destination changed')
        if core.snapshot(client, payload['folder'], payload['uidvalidity'], [i['uid'] for i in payload['items']]) != payload['items']:
            raise core.SafetyError('Message snapshot changed before exception review')
    return manager.preview_exception(plan, deviations)


def execute(account, store, proposal_id, expected_hash, actor_reference, service_factory):
    """Owner-approved exact revision or Trash exception; never implicit fallback."""
    if os.environ.get('ICLOUD_MAIL_ACCESS', 'read-only') != 'write-enabled':
        raise core.SafetyError('Managed execution requires separately authorized writes')
    manager, access, _ = context(account)
    with manager.locked(), ledger_guard(access) as entries, mail_guard(store, account):
        proposal, audit = manager._admit(proposal_id, expected_hash, actor_reference)
        if proposal['kind'] == 'policy-change':
            if manager.current() != proposal['payload']['before']:
                raise core.SafetyError('Before-policy changed; review a fresh preview')
            manager.store.write_once(manager.store.path(proposal_id, '.started'), audit)
            target = manager.store.path(proposal_id, '.next')
            manager.store.write_once(target, proposal['payload']['after'])
            os.replace(target, manager.store.root / 'policy.json')
            result = {'status':'completed','policy_sha256':core.digest(proposal['payload']['after']), 'revision':proposal['payload']['after']['revision']}
            manager.store.finish(proposal_id, result)
            return result
        if proposal['kind'] != 'one-time-trash':
            raise core.SafetyError('Unsupported managed operation')
        bound = proposal['payload']
        if core.digest(manager.current()) != bound['policy_sha256']:
            raise core.SafetyError('Policy revision changed after exception review')
        plan_id = bound['plan']['plan_id']
        plan = store.proposal(plan_id)
        if plan != bound['plan'] or core.digest(plan) != bound['plan_sha256']:
            raise core.SafetyError('Original mail plan changed')
        payload = plan['payload']; uids = [i['uid'] for i in payload['items']]
        if payload['account'] != account:
            raise core.SafetyError('Exception belongs to a different account')
        # Re-run schema/deviation validation under the same policy lock without
        # creating another proposal or approval. Exact immutable preview still binds.
        policy = manager.current()['policy']
        if len(uids) > policy['max_batch'] or payload['destination'] != policy['destinations'].get('trash'):
            raise core.SafetyError('Exception exceeds current operation/destination scope')
        now = manager.clock()
        recent = {(e['folder'],e['uidvalidity'],n) for e in entries if e['time'] >= now-86400 for n in e['uids']}
        requested = {(payload['folder'],payload['uidvalidity'],n) for n in uids}
        if policy['max_24h'] is not None and len(recent | requested) > policy['max_24h']:
            raise core.SafetyError('Managed exception exceeds existing aggregate quota')
        service = service_factory()
        if service.account != account:
            raise core.SafetyError('Backend account mismatch')
        with service.imap() as client:
            if core.role_folder(client,'Trash',service.overrides) != payload['destination']:
                raise core.SafetyError('Trash role changed')
            if core.snapshot(client,payload['folder'],payload['uidvalidity'],uids) != payload['items']:
                raise core.SafetyError('Exact exception snapshot changed; no write')
            manager.store.write_once(manager.store.path(proposal_id,'.started'),audit)
            store.write_once(store.path(plan_id,'.started'),dict(audit, account=account,
                              managed_proposal=proposal_id, sha256=core.digest(plan)))
            action_id = hashlib.sha256(('managed-exception:'+proposal_id).encode()).hexdigest()[:32]
            access.audit.write_once(access.audit.path(action_id,'.started'),
                                    {'account':account,'time':now,'folder':payload['folder'],'uidvalidity':payload['uidvalidity'],
                                     'uids':uids,'destination':payload['destination'],'proposal_sha256':expected_hash})
            try:
                result = core.safe_move(client,payload['folder'],payload['uidvalidity'],uids,payload['destination'],payload['items'])
            except Exception:
                result = {'status':'failed_or_uncertain','do_not_retry':True}
            store.finish(plan_id,result)
            manager.store.finish(proposal_id,result if result.get('status') != 'moved' else dict(result,status='completed'))
            access.audit.finish(action_id,{'status':'completed' if result.get('status') == 'moved' else 'uncertain','result':result})
            return result


def register_tools(mcp, safe, service_factory):
    read={'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}
    write={'readOnlyHint':False,'destructiveHint':True,'idempotentHint':False,'openWorldHint':True}
    def owner():
        return os.environ.get('ICLOUD_MAIL_ACCOUNT','')

    @mcp.tool(annotations=read)
    @safe
    def policy_status(proposal_id: str | None = None) -> dict:
        """Read effective policy revision and its exact hash; no credential access."""
        manager,_,_=context(owner());current=manager.current()
        result = {'current':current,'sha256':core.digest(current),'authorization_mode':manager.approval_policy}
        if proposal_id is not None:
            proposal = manager.store.read(manager.store.path(proposal_id,'.json'))
            result['proposal_sha256'] = core.digest(proposal)
            if manager.store.path(proposal_id,'.result').exists():
                result['outcome'] = manager.store.read(manager.store.path(proposal_id,'.result'))
            elif manager.store.path(proposal_id,'.started').exists():
                result['outcome'] = {'status':'started_or_uncertain','do_not_retry':True}
            elif manager.store.path(proposal_id,'.revoked').exists():
                result['outcome'] = {'status':'revoked'}
            else:
                result['outcome'] = {'status':'expired' if manager.clock() >= proposal['expires'] else 'awaiting_configured_authorization'}
        return result

    @mcp.tool(annotations=write)
    @safe
    def policy_prepare_change(replacement: dict) -> dict:
        """Preview an exact persistent mail-policy change; does not apply it."""
        manager,_,base=context(owner())
        if base['expires'] is not None and (replacement.get('expires') is None or replacement['expires'] > base['expires']):
            raise core.SafetyError('Managed policy cannot extend the base grant lifetime')
        return manager.preview_change(replacement)

    @mcp.tool(annotations=write)
    @safe
    def policy_prepare_exception(mail_plan_id: str, deviations: list[str]) -> dict:
        """Preview one exact recoverable-Trash exception; not an approval or move."""
        context(owner())
        plan = PlanStore().proposal(mail_plan_id)
        if plan['kind'] != 'trash' or plan['payload'].get('account') != owner():
            raise core.SafetyError("Only this account's exact Trash plan can be proposed")
        return prepare_exception(service_factory(),mail_plan_id,deviations)

    @mcp.tool(annotations=write)
    @safe
    def policy_execute_approved(proposal_id: str, expected_hash: str, actor_reference: str) -> dict:
        """Execute exact reviewed proposal under configured receipt/trusted-client policy."""
        return execute(owner(),PlanStore(),proposal_id,expected_hash,actor_reference,service_factory)

    @mcp.tool(annotations=write)
    @safe
    def policy_revoke_proposal(proposal_id: str) -> dict:
        """Revoke one unstarted proposal; cannot recall a started provider action."""
        manager,_,_=context(owner());manager.revoke(proposal_id);return {'status':'revoked'}
