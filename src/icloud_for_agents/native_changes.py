"""Shared native approval executor. Concrete write adapters are gated separately.

No native write adapter is registered by this module. Adapters must implement fixed
operations, preserve unrelated native data, and provide independent readback.
"""
from contextlib import contextmanager
import fcntl
import os
import stat

from .core import SafetyError, NativeOperationRefused, canonical, digest
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

    def assert_settled(self,correction=None):
        for started in self.store.root.glob('*.started'):
            previous=self.store.read(self.store.path(started.stem,'.json'))
            if previous['payload'].get('account')!=self.account:
                continue
            if not self.store.operation_settled(started.stem):
                if not (self.store._reconciliation_completed(started.stem) or (correction and correction.get('native_recovery_of')==started.stem and self.store._native_recovery_for(started.stem,correction)) or (correction and correction.get('native_correction_of')==started.stem and self.store._native_correction_for(started.stem,correction))):
                    raise SafetyError('Prior write unfinished or uncertain; reconcile before another native write')

    def prepare_update_correction(self,original_id):
        """Propose one explicit same-object correction; never retry an unknown mutation.

        Only one fully identified reminder update that failed readback qualifies.
        Current state and the original requested final fields remain reviewable.
        The ordinary strict/trusted authorization applies to the new plan.
        """
        if self.name!='reminders':raise SafetyError('Correction is limited to known reminder updates')
        import uuid
        context=self.context()
        with self.serialized():
            original=self.store.read(self.store.path(original_id,'.json'));result=self.store.read(self.store.path(original_id,'.result'))
            changes=original.get('payload',{}).get('changes',[]);attempts=result.get('attempted',[])
            if (original.get('kind')!=self.kind or original.get('native_correction_of')
                    or original['payload'].get('account')!=self.account
                    or original['payload'].get('context',{}).get('source_ids')!=context['source_ids']
                    or result.get('status')!='failed_or_uncertain' or result.get('phase')!='verification'
                    or len(changes)!=1 or len(attempts)!=1 or changes[0].get('operation')!='update_reminder'):
                raise SafetyError('Only one complete known-ID reminder update can be corrected')
            item=changes[0];attempt=attempts[0]
            if (attempt.get('index')!=0 or attempt.get('operation')!='update_reminder'
                    or attempt.get('result',{}).get('id')!=item.get('id')
                    or self.store.read(self.store.path(original_id,'.attempt-0'))!={'plan_sha256':digest(original),'attempt':attempt}):
                raise SafetyError('Exact immutable returned identity required')
            expected=dict(item['before']['item']);expected.update(item['fields'])
            current=self.adapter.reader.read('get',id=item['id'])['item']
            for key in ('source_id','list_id','completed','url','has_recurrence','has_alarms'):
                if current.get(key)!=expected.get(key):raise SafetyError('Unrelated effects or external changes require separate review')
            fields={key:expected.get(key) for key in ('title','notes','priority','due','start')}
            for key in ('due','start'):
                if isinstance(fields[key],dict):fields[key]={k:v for k,v in fields[key].items() if k in {'year','month','day','hour','minute','timezone'} and v is not None}
            repair=self.adapter.prepare({'operation':'update_reminder','source_id':item['source_id'],'id':item['id'],'fields':fields})
            self.check_item(repair,context)
            correction_id=uuid.uuid4().hex
            correction={'plan_id':correction_id,'kind':self.kind,'created':self.store.clock(),'expires':self.store.clock()+1800,
                'native_correction_of':original_id,'payload':{'account':self.account,'context':context,'changes':[repair],
                'original_failure_sha256':digest(result),'purpose':'restore the originally requested public fields on the exact returned reminder; no creation or replay'}}
            # Reserve once before writing the proposal. A crash may strand repair,
            # but cannot authorize two corrective writes.
            self.store.write_once(self.store.path(original_id,'.native-correction'),
                {'status':'reserved_known_update_correction','original_sha256':digest(original),
                 'original_result_sha256':digest(result),'correction_id':correction_id,'correction_sha256':digest(correction)})
            self.store.write_once(self.store.path(correction_id,'.json'),correction)
            from .approvals import authorization_path
            path,receipt=authorization_path(correction)
            return {'plan_id':correction_id,'review':correction,'sha256':digest(correction),
                    'approval_required':True,'authorization_path':path,'local_receipt_required':receipt}

    def reconcile_correction(self,correction_id):
        """Verify both correction and original requested outcome; preserve both audits."""
        self.context()
        with self.serialized():
            correction=self.store.read(self.store.path(correction_id,'.json'));original_id=correction.get('native_correction_of')
            if not original_id or not self.store._native_correction_for(original_id,correction):raise SafetyError('Invalid correction reservation')
            if correction['payload'].get('account')!=self.account or correction['payload'].get('context')!=self.context():raise SafetyError('Correction scope or backend changed')
            result=self.store.read(self.store.path(correction_id,'.result'))
            original=self.store.read(self.store.path(original_id,'.json'));original_result=self.store.read(self.store.path(original_id,'.result'))
            if (result.get('status')!='applied' or not self.adapter.verify(original['payload']['changes'][0],original_result['attempted'][0]['result'])):
                raise SafetyError('Original requested state still does not verify; uncertainty retained')
            self.store.write_once(self.store.path(original_id,'.native-correction-result'),
                {'status':'original_requested_fields_verified_after_correction','correction_id':correction_id,
                 'correction_result_sha256':digest(result),'original_result_sha256':digest(original_result),'checked_at':self.store.clock()})
            return self.store.status(original_id)

    def reconcile_created(self,plan_id):
        """Local reviewed read-only reconciliation of known returned creation IDs.

        No retry, generic uncertainty clear, model-supplied proof or audit rewrite.
        Other failure phases and incomplete batches remain blocked.
        """
        from .native_access import access_status
        status=access_status(self.name)
        if status['effective_access']=='disabled':raise SafetyError('Native access is disabled')
        with self.serialized():
            plan=self.store.read(self.store.path(plan_id,'.json'))
            result=self.store.read(self.store.path(plan_id,'.result'))
            context=plan['payload'].get('context',{})
            if (plan.get('kind')!=self.kind or plan['payload'].get('account')!=self.account
                    or context.get('source_ids')!=sorted(status['source_ids'])
                    or context.get('backend')!=self.adapter.backend_id):
                raise SafetyError('Reconciliation scope, backend or account mismatch')
            items=plan['payload'].get('changes',[]);attempts=result.get('attempted',[])
            allowed={'create_note','create_folder','create_reminder','create_list'}
            if (result.get('status')!='failed_or_uncertain' or result.get('phase')!='verification'
                    or not items or len(attempts)!=len(items) or any(i.get('operation') not in allowed for i in items)):
                raise SafetyError('Only complete known-ID creation batches with failed readback can be reconciled')
            hashes=[]
            for index,(item,attempt) in enumerate(zip(items,attempts)):
                entry=self.store.read(self.store.path(plan_id,'.attempt-'+str(index)))
                if (entry!={'plan_sha256':digest(plan),'attempt':attempt} or attempt.get('index')!=index
                        or attempt.get('operation')!=item['operation'] or not self.adapter.verify(item,attempt['result'])):
                    raise SafetyError('Known created object does not verify; uncertainty retained')
                hashes.append(digest(entry))
            self.store.write_once(self.store.path(plan_id,'.native-reconciled'),
                {'status':'native_readback_verified','plan_sha256':digest(plan),'result_sha256':digest(result),
                 'attempts_sha256':digest(attempts),'attempt_record_hashes':hashes,'checked_at':self.store.clock(),
                 'method':'independent adapter readback of exact returned creation identities; no mutation or human receipt'})
            return self.store.status(plan_id)

    def execute(self,plan_id):
        context=self.context()
        with self.serialized():
            candidate=self.store.proposal(plan_id)
            self.assert_settled(candidate)
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
            attempted=[]
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
                    attempt={'index':index,'operation':item['operation'],'result':outcome}
                    attempted.append(attempt)
                    self.store.write_once(self.store.path(plan_id,'.attempt-'+str(index)),
                        {'plan_sha256':digest(plan),'attempt':attempt})
                    phase='verification'
                    if self.adapter.verify(item,outcome) is not True:
                        raise SafetyError('Native final readback did not verify')
                    completed.append({'index':index,'operation':item['operation'],'result':outcome})
                result={'status':'applied','attempted':attempted,'completed':completed,'do_not_retry':True,
                        'sync_status':'unconfirmed','plan_sha256':digest(plan)}
            except Exception as exc:
                result={'status':'failed_or_uncertain','attempted':attempted,'completed':completed,'do_not_retry':True,
                        'phase':phase,'message':'Inspect exact objects and reconcile; no automatic retry or rollback'}
                if isinstance(exc,SafetyError):result['safety_reason']=str(exc)
                expected_request={'protocol_version':1,'service':self.name,'action':'apply','source_ids':[item['source_id']],'change':item}
                if (isinstance(exc,NativeOperationRefused) and phase=='mutation'
                        and exc.evidence.get('mutation_dispatched') is False
                        and exc.evidence.get('request_sha256')==digest(expected_request)
                        and isinstance(exc.evidence.get('helper_response_sha256'),str)
                        and len(exc.evidence['helper_response_sha256'])==64
                        and all(c in '0123456789abcdef' for c in exc.evidence['helper_response_sha256'])):
                    result.update(status='refused_without_dispatch',outcome='not_applied' if not completed else 'partially_applied_then_refused',
                                  refused_index=index,refused_item_mutation_dispatched=False,refusal_evidence=exc.evidence,
                                  plan_sha256=digest(plan),message='The helper confirmed no mutation dispatch for this item. Earlier verified items are listed; remaining items were not attempted. Prepare a new reviewed plan after resolving the refusal.')
            return self.store.finish(plan_id,result)
