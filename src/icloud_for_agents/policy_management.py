"""Policy proposal, review and audit core for the optional production adapter.

A workspace is explicitly supplied; no live grant/settings paths are inferred.
Trusted-client admission is an assertion, not independently authenticated consent.
An adapter/activation review is required before connecting this to live actions.
"""
from contextlib import contextmanager
import fcntl
import json
import math
import os
import re
import stat
import time
import uuid
from .approvals import PlanStore
from .core import SafetyError, digest, batch


def validate_policy(value):
    keys = {'account', 'operations', 'source_folders', 'destinations', 'max_batch', 'max_24h', 'expires', 'protect_starred'}
    if not isinstance(value, dict) or set(value) != keys:
        raise SafetyError('Unknown policy fields; instructions cannot change enforcement')
    if not isinstance(value['account'], str) or not value['account'] or any(ord(c) < 33 for c in value['account']):
        raise SafetyError('Exact account required')
    ops = value['operations']; folders = value['source_folders']; destinations = value['destinations']
    if not isinstance(ops, list) or not ops or set(ops) - {'archive', 'mark_read', 'trash'} or len(set(ops)) != len(ops):
        raise SafetyError('Policy cannot authorize sends, DAV, purge or security changes')
    if not isinstance(folders, list) or not 1 <= len(folders) <= 10 or len(set(folders)) != len(folders):
        raise SafetyError('Exact bounded source folders required')
    if not isinstance(destinations, dict) or set(destinations) != set(ops) - {'mark_read'}:
        raise SafetyError('Exact move destinations required')
    names = folders + list(destinations.values())
    if any(not isinstance(v, str) or not 1 <= len(v) <= 1000 or v == '*' or any(ord(c) < 32 for c in v) for v in names):
        raise SafetyError('Wildcards and control characters are refused')
    if set(folders) & set(destinations.values()) or len(set(destinations.values())) != len(destinations):
        raise SafetyError('Source and destination roles must be distinct')
    if type(value['max_batch']) is not int or not 1 <= value['max_batch'] <= 100:
        raise SafetyError('Hard batch bounds cannot be overridden')
    if value['max_24h'] is not None and (type(value['max_24h']) is not int or value['max_24h'] < value['max_batch']):
        raise SafetyError('Invalid aggregate quota')
    if value['expires'] is not None and (type(value['expires']) not in (int, float) or not 0 < value['expires'] < float('inf')):
        raise SafetyError('Invalid policy expiry')
    if value['protect_starred'] is not True:
        raise SafetyError('Standing starred protection cannot be disabled; use exact exceptions')
    return value


class PolicyManager:
    def __init__(self, workspace, *, approval_policy='local-receipt', clock=time.time):
        if approval_policy not in {'local-receipt', 'trusted-client'}:
            raise SafetyError('Unknown owner-selected approval policy')
        self.store = PlanStore(workspace, clock=clock)
        self.clock, self.approval_policy = clock, approval_policy

    @contextmanager
    def locked(self):
        self.store.ready()
        fd = os.open(self.store.root / 'management.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise SafetyError('Invalid policy lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def initialize_offline(self, policy):
        """Explicit workspace bootstrap, never activation of an existing grant."""
        validate_policy(policy)
        with self.locked():
            self.store.write_once(self.store.root / 'policy.json', {'revision': 1, 'policy': policy})

    def current(self):
        record = self.store.read(self.store.root / 'policy.json')
        validate_policy(record['policy'])
        if (self.store.root / 'policy.revoked').exists():
            raise SafetyError('Managed policy revoked; no new admission')
        expiry = record['policy']['expires']
        if expiry is not None and self.clock() >= expiry:
            raise SafetyError('Managed policy expired')
        return record

    def preview_change(self, replacement):
        validate_policy(replacement)
        current = self.current()
        if replacement['expires'] is not None and replacement['expires'] <= self.clock():
            raise SafetyError('Replacement policy already expired')
        if replacement['account'] != current['policy']['account']:
            raise SafetyError('Account changes require separate credential/access setup')
        return self._prepare('policy-change', {'before': current, 'after': {'revision': current['revision'] + 1, 'policy': replacement}})

    def preview_exception(self, plan, deviations):
        current = self.current(); policy = current['policy']; payload = plan.get('payload', {})
        if (plan.get('kind') != 'trash' or payload.get('account') != policy['account'] or 'trash' not in policy['operations']
                or payload.get('destination') != policy['destinations'].get('trash') or payload.get('folder') == payload.get('destination')):
            raise SafetyError('Exceptions cover recoverable Trash only in the same account/destination')
        if not isinstance(deviations, list) or not deviations or set(deviations) - {'starred', 'source_folder'} or len(set(deviations)) != len(deviations):
            raise SafetyError('Only explicit starred/source-folder exceptions are representable')
        folder = payload.get('folder')
        if not isinstance(folder,str) or not 1 <= len(folder) <= 1000 or folder == '*' or any(ord(c)<32 for c in folder):
            raise SafetyError('Exact literal source folder required')
        items = payload.get('items', []); uids = batch([i['uid'] for i in items])
        if len(uids) != len(set(uids)) or len(uids) > policy['max_batch'] or type(payload.get('uidvalidity')) is not int or payload['uidvalidity'] <= 0:
            raise SafetyError('Exact bounded message identities required')
        for item in items:
            if (not isinstance(item.get('flags'), list) or '\\Deleted' in item['flags']
                    or type(item.get('size')) is not int or item['size'] < 0
                    or not re.fullmatch('[0-9a-f]{64}', item.get('header_sha256', ''))):
                raise SafetyError('Exact nondeleted snapshot required')
        actual = set()
        if any('\\Flagged' in i['flags'] for i in items): actual.add('starred')
        if payload.get('folder') not in policy['source_folders']: actual.add('source_folder')
        if actual != set(deviations):
            raise SafetyError('Declared deviations must exactly describe this batch')
        if type(plan.get('expires')) not in (int,float) or not math.isfinite(plan['expires']) or plan['expires'] <= self.clock():
            raise SafetyError('Original plan expired')
        return self._prepare('one-time-trash', {'policy_revision': current['revision'], 'policy_sha256': digest(current),
                             'plan': plan, 'plan_sha256': digest(plan), 'deviations': sorted(deviations)},
                             expires=min(plan['expires'], self.clock()+300))

    def _prepare(self, kind, payload, expires=None):
        proposal = {'plan_id': uuid.uuid4().hex, 'kind': kind, 'created': self.clock(),
                    'expires': expires if expires is not None else self.clock()+300, 'payload': payload}
        self.store.write_once(self.store.path(proposal['plan_id'], '.json'), proposal)
        return {'proposal': proposal, 'sha256': digest(proposal), 'executes_changes': False}

    def _admit(self, proposal_id, expected_hash, actor_reference):
        for started in self.store.root.glob('*.started'):
            outcome = started.with_suffix('.result')
            if not outcome.exists() or self.store.read(outcome).get('status') != 'completed':
                raise SafetyError('Prior managed-policy outcome unfinished or uncertain; reconcile before new admission')
        proposal = self.store.read(self.store.path(proposal_id, '.json'))
        if (type(proposal.get('expires')) not in (int,float) or not math.isfinite(proposal['expires'])
                or digest(proposal) != expected_hash or self.clock() >= proposal['expires']
                or self.store.path(proposal_id, '.revoked').exists() or self.store.path(proposal_id, '.started').exists()):
            raise SafetyError('Changed, expired, revoked or already consumed proposal')
        if not isinstance(actor_reference, str) or not 1 <= len(actor_reference) <= 300 or any(ord(c) < 32 for c in actor_reference):
            raise SafetyError('Bounded authorization provenance required, not an approved boolean')
        if self.approval_policy == 'local-receipt':
            receipt = self.store.read(self.store.path(proposal_id, '.approved'))
            if receipt.get('sha256') != expected_hash or receipt.get('plan_id') != proposal_id:
                raise SafetyError('Genuine exact local receipt required')
            provenance = 'local-receipt'
        else:
            provenance = 'trusted-client assertion; not independently authenticated owner consent'
        return proposal, {'time': self.clock(), 'sha256': expected_hash, 'actor_reference': actor_reference, 'authorization': provenance}

    def apply_change_offline(self, proposal_id, expected_hash, actor_reference):
        with self.locked():
            proposal, audit = self._admit(proposal_id, expected_hash, actor_reference)
            if proposal['kind'] != 'policy-change' or self.current() != proposal['payload']['before']:
                raise SafetyError('Wrong operation or stale before-policy')
            self.store.write_once(self.store.path(proposal_id, '.started'), audit)
            # Journal first. An interrupted replacement remains started/uncertain.
            temp = self.store.path(proposal_id, '.next')
            self.store.write_once(temp, proposal['payload']['after'])
            os.replace(temp, self.store.root / 'policy.json')
            self.store.write_once(self.store.path(proposal_id, '.result'), {'status':'completed', 'policy_sha256':digest(self.current())})
            return self.current()

    def consume_exception_offline(self, proposal_id, expected_hash, actor_reference, current_plan, current_snapshot):
        with self.locked():
            proposal, audit = self._admit(proposal_id, expected_hash, actor_reference)
            payload = proposal['payload']
            if (proposal['kind'] != 'one-time-trash' or digest(self.current()) != payload['policy_sha256']
                    or current_plan != payload['plan'] or digest(current_plan) != payload['plan_sha256']
                    or current_snapshot != current_plan['payload']['items'] or self.clock() >= current_plan['expires']):
                raise SafetyError('Exception scope or current snapshot changed')
            self.store.write_once(self.store.path(proposal_id, '.started'), audit)
            return {'status':'reserved_offline', 'operation':'recoverable_trash', 'plan_sha256':payload['plan_sha256'],
                    'deviations':payload['deviations'], 'not_a_live_execution_permission':True}

    def revoke(self, proposal_id):
        with self.locked():
            self.store.read(self.store.path(proposal_id, '.json'))
            if self.store.path(proposal_id, '.started').exists():
                raise SafetyError('Cannot recall started action; reconcile outcome')
            self.store.write_once(self.store.path(proposal_id, '.revoked'), {'time':self.clock()})

    def revoke_policy_offline(self, expected_policy_hash):
        """Stop this workspace; does not change an installed standing grant."""
        with self.locked():
            current = self.current()
            if digest(current) != expected_policy_hash:
                raise SafetyError('Policy changed before revocation')
            self.store.write_once(self.store.root / 'policy.revoked',
                                  {'time':self.clock(), 'policy_sha256':expected_policy_hash})
