"""Owner-reviewed observation of an unchanged note after one uncertain edit.

No native mutation, replay, causal dispatch claim, or original audit rewrite.
"""
from .approvals import PlanStore
from .core import SafetyError,digest
from .native_changes import NativeChanges
from .native_notes import NotesCreateAdapter
from .native_edit_observation import inspect_uncertain_note_edit
from .native_protocol import mutation_request

class NotesEditRecovery:
    def __init__(self,account,store=None,adapter=None):
        self.account=account;self.store=store or PlanStore();self.adapter=adapter or NotesCreateAdapter()
        self.engine=NativeChanges('notes',account,self.adapter,self.store)

    def original(self,identity):
        plan=self.store.read(self.store.path(identity,'.json'));result=self.store.read(self.store.path(identity,'.result'))
        items=plan.get('payload',{}).get('changes',[]);attempts=result.get('attempted')
        if (plan.get('kind')!='notes_change' or plan.get('payload',{}).get('account')!=self.account
                or len(items)!=1 or items[0].get('operation') not in {'append_note','prepend_note','replace_text','update_note'}
                or result.get('status')!='failed_or_uncertain' or result.get('phase') not in {'mutation','verification'}
                or result.get('completed')!=[] or not isinstance(attempts,list) or len(attempts)>1
                or self.store.read(self.store.path(identity,'.started')).get('sha256')!=digest(plan)):
            raise SafetyError('Recovery requires one owned uncertain edit of an exact pre-existing note')
        expected=set()
        if attempts:
            attempt=attempts[0];expected={self.store.path(identity,'.attempt-0')}
            if (attempt.get('index')!=0 or attempt.get('operation')!=items[0]['operation'] or attempt.get('result',{}).get('id')!=items[0]['id']
                    or self.store.read(self.store.path(identity,'.attempt-0'))!={'plan_sha256':digest(plan),'attempt':attempt}):
                raise SafetyError('Known edit attempt evidence is inconsistent')
        if set(self.store.root.glob(identity+'.attempt-*'))!=expected:raise SafetyError('Unexpected edit dispatch evidence')
        return plan,result,items[0]

    def observation(self,original_id,folder_id):
        result=inspect_uncertain_note_edit(self.engine,original_id,folder_id)
        if result.get('observable_content_matches_before') is not True:
            raise SafetyError('The current note does not match the reviewed pre-edit content and attachments')
        return result

    def prepare(self,original_id,folder_id):
        self.engine.context(True)
        with self.engine.serialized():
            plan,result,item=self.original(original_id)
            if self.store._native_edit_recovery_completed(original_id):raise SafetyError('An observation has already been accepted for this edit')
            observed=self.observation(original_id,folder_id)
            payload={'account':self.account,'context':self.engine.context(True),'original_id':original_id,
                'original_sha256':digest(plan),'original_result_sha256':digest(result),'original_item_sha256':digest(item),
                'request_sha256':digest(mutation_request('notes',item)),'operation':'accept_observed_unchanged_note',
                'observed':observed,'effects':{'native_data_changed':False,'original_result_unchanged':True,'old_plan_replay':'forbidden',
                    'decision':'Accept this exact current note and its observed pre-edit content after review.',
                    'uncertainty':'The original dispatch and causal history remain unproven; this does not claim the edit succeeded or was never sent.',
                    'future_actions':'Fresh plans still require current scope, fidelity and approval checks.'}}
            return self.store.prepare('notes_edit_recovery',payload)

    def checked(self,identity,*,pending=False):
        plan=self.store.proposal(identity) if pending else self.store.read(self.store.path(identity,'.json'));payload=plan.get('payload',{});original_id=payload.get('original_id')
        if not original_id or not self.store._native_edit_recovery_for(original_id,plan):raise SafetyError('Invalid edit observation binding or a prior observation was accepted')
        if payload.get('context')!=self.engine.context(True):raise SafetyError('Edit observation access, backend or approval policy changed')
        original,result,item=self.original(original_id)
        if self.observation(original_id,payload['observed']['observed_folder_id'])!=payload['observed']:
            raise SafetyError('Observed note or terminal exchange changed since review')
        return plan

    def proof(self,plan,result):
        original_id=plan['payload']['original_id'];record={'status':'reviewed_unchanged_note_accepted','recovery_id':plan['plan_id'],
             'recovery_result_sha256':digest(result),'original_result_sha256':plan['payload']['original_result_sha256'],'observed_sha256':result['after_sha256']}
        path=self.store.path(original_id,'.native-edit-recovery-result')
        if path.exists():
            if self.store.read(path)!=record:raise SafetyError('Edit observation proof conflicts with an earlier association')
        else:self.store.write_once(path,record)

    def execute(self,identity):
        self.engine.context(True)
        with self.engine.serialized():
            plan=self.checked(identity,pending=True);self.engine.assert_settled(plan);self.store.consume(identity,self.account)
            try:
                if self.engine.context(True)!=plan['payload']['context'] or self.observation(plan['payload']['original_id'],plan['payload']['observed']['observed_folder_id'])!=plan['payload']['observed']:
                    raise SafetyError('Observed state changed')
            except Exception:
                return self.store.finish(identity,{'status':'completed','recovery_state':'not_accepted','native_data_changed':False,'original_result_unchanged':True,'message':'Original uncertainty remains; prepare a fresh observation after resolving the read failure.'})
            result={'status':'completed','recovery_state':'observed_unchanged_note_accepted_after_review','native_data_changed':False,
                    'original_result_unchanged':True,'causal_dispatch_proven':False,'after_sha256':digest(plan['payload']['observed']),'do_not_retry':True}
            self.store.finish(identity,result);self.proof(plan,result);return result

    def finalize(self,identity):
        self.engine.context(True)
        with self.engine.serialized():
            stored=self.store.read(self.store.path(identity,'.json'));original_id=stored.get('payload',{}).get('original_id')
            if stored.get('kind')=='notes_edit_recovery' and stored['payload'].get('account')==self.account and original_id and self.store._native_edit_recovery_completed(original_id):
                if self.store.read(self.store.path(original_id,'.native-edit-recovery-result'))['recovery_id']!=identity:raise SafetyError('Another observation was finalized for this edit')
                return self.store.status(original_id)
            plan=self.checked(identity);result=self.store.read(self.store.path(identity,'.result'))
            if (self.store.read(self.store.path(identity,'.started')).get('sha256')!=digest(plan) or result.get('status')!='completed'
                    or result.get('recovery_state')!='observed_unchanged_note_accepted_after_review' or result.get('after_sha256')!=digest(plan['payload']['observed'])):
                raise SafetyError('Only a successful admitted edit observation can be finalized')
            self.proof(plan,result);return self.store.status(plan['payload']['original_id'])

    def abandon(self,identity):
        from .native_access import access_status
        access_status('notes')
        with self.engine.serialized():
            plan=self.store.read(self.store.path(identity,'.json'));payload=plan.get('payload',{})
            if plan.get('kind')!='notes_edit_recovery' or payload.get('account')!=self.account:raise SafetyError('Observation belongs to another owner or operation')
            if self.store.path(payload['original_id'],'.native-edit-recovery-result').exists():raise SafetyError('A finalized observation cannot be withdrawn')
            marker=self.store.path(identity,'.native-edit-recovery-abandoned');record={'recovery_sha256':digest(plan),'native_data_changed':False}
            if marker.exists():
                if self.store.read(marker)!=record:raise SafetyError('Observation withdrawal evidence changed')
            else:self.store.write_once(marker,record)
            if not self.store.path(identity,'.started').exists():
                cancelled=self.store.path(identity,'.cancelled')
                if not cancelled.exists():self.store.write_once(cancelled,{'plan_sha256':digest(plan),'cancelled_at':self.store.clock()})
            elif not self.store.path(identity,'.result').exists():self.store.finish(identity,{'status':'completed','recovery_state':'not_accepted','native_data_changed':False,'original_result_unchanged':True})
            return {'status':'abandoned','original_uncertainty_retained':True,'native_data_changed':False}
