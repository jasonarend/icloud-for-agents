"""Shared native approval executor. Concrete write adapters are gated separately.

No native write adapter is registered by this module. Adapters must implement fixed
operations, preserve unrelated native data, and provide independent readback.
"""
from contextlib import contextmanager
import fcntl
import os
import stat

from .core import SafetyError, canonical, digest
from .native_access import require_write


class NativeChanges:
    def __init__(self, name, account, adapter, store):
        if name not in {'notes', 'reminders'}:
            raise SafetyError('Unknown native service')
        self.name, self.account, self.adapter, self.store = name, account, adapter, store
        self.kind = name + '_change'

    def context(self):
        status = require_write(self.name)
        return {'service':self.name,'source_ids':sorted(status['source_ids']),
                'backend':self.adapter.backend_id,'approval_policy':status['approval_policy']}

    def prepare(self, changes):
        context = self.context()
        if not isinstance(changes,list) or not 1 <= len(changes) <= 20:
            raise SafetyError('Prepare 1..20 exact native changes')
        if len(canonical(changes)) > 100000:
            raise SafetyError('Native change batch exceeds review limit')
        prepared = []
        seen = set()
        for change in changes:
            if not isinstance(change,dict) or change.get('operation') not in self.adapter.operations:
                raise SafetyError('Unsupported native operation')
            item = self.adapter.prepare(change)
            self.check_item(item,context)
            identity = item.get('id')
            if identity:
                if identity in seen:
                    raise SafetyError('Repeated target in one batch; prepare sequential changes separately')
                seen.add(identity)
            prepared.append(item)
        payload = {'account':self.account,'context':context,'changes':prepared,
                   'transaction':'per-item; no atomic backend transaction or automatic rollback'}
        return self.store.prepare(self.kind,payload)

    def check_item(self,item,context):
        if not isinstance(item,dict) or item.get('source_id') not in context['source_ids']:
            raise SafetyError('Native operation targets an unselected source')
        if item.get('operation') not in self.adapter.operations:
            raise SafetyError('Unsupported native operation')
        if item.get('fidelity_verified') is not True:
            raise SafetyError('Native operation cannot preserve required content')
        if not isinstance(item.get('effects'),dict):
            raise SafetyError('Native operation must describe its effects')

    @contextmanager
    def serialized(self):
        self.store.ready()
        fd = os.open(self.store.root/'native-execution.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:
                raise SafetyError('Invalid native execution lock')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
        except BlockingIOError:
            raise SafetyError('Another native execution is active; inspect status') from None
        finally:
            os.close(fd)

    def assert_settled(self):
        for started in self.store.root.glob('*.started'):
            previous=self.store.read(self.store.path(started.stem,'.json'))
            if previous['payload'].get('account')!=self.account:
                continue
            result=self.store.path(started.stem,'.result')
            if not result.exists() or self.store.read(result).get('status') not in {'applied','moved','sent','completed'}:
                if not self.store._reconciliation_completed(started.stem):
                    raise SafetyError('Prior write unfinished or uncertain; reconcile before another native write')

    def execute(self,plan_id):
        context=self.context()
        with self.serialized():
            self.assert_settled()
            candidate=self.store.proposal(plan_id)
            payload=candidate['payload']
            if candidate['kind']!=self.kind or payload.get('account')!=self.account:
                raise SafetyError('Wrong native service or account')
            if payload.get('context')!=context:
                raise SafetyError('Native scope, backend or approval policy changed')
            changes=payload.get('changes')
            if not isinstance(changes,list) or not 1 <= len(changes) <= 20:
                raise SafetyError('Invalid native batch')
            for item in changes:
                self.check_item(item,context)
                if self.adapter.snapshot(item)!=item.get('before'):
                    raise SafetyError('Native object changed since review; no writes admitted')
            plan=self.store.consume(plan_id,self.account)
            completed=[]
            phase='pre_dispatch'
            try:
                for index,item in enumerate(plan['payload']['changes']):
                    if self.context()!=context:
                        raise SafetyError('Native access changed between items')
                    phase='snapshot_recheck'
                    if self.adapter.snapshot(item)!=item.get('before'):
                        raise SafetyError('Native object changed immediately before dispatch')
                    phase='mutation'
                    outcome=self.adapter.apply(item)
                    phase='verification'
                    if self.adapter.verify(item,outcome) is not True:
                        raise SafetyError('Native final readback did not verify')
                    completed.append({'index':index,'operation':item['operation'],'result':outcome})
                result={'status':'applied','completed':completed,'do_not_retry':True,
                        'sync_status':'unconfirmed','plan_sha256':digest(plan)}
            except Exception:
                result={'status':'failed_or_uncertain','completed':completed,'do_not_retry':True,
                        'phase':phase,'message':'Inspect exact objects and reconcile; no automatic retry or rollback'}
            return self.store.finish(plan_id,result)
