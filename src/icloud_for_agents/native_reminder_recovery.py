"""Explicitly accept one observed Reminder state without replay or mutation.

The original failure and lack of causal proof remain visible. This recovery
requires request-bound terminal evidence retained by the original execution.
"""
from .approvals import PlanStore
from .core import SafetyError, digest
from .native_changes import NativeChanges
from .native_reminder_observation import inspect_uncertain_reminder
from .native_reminders import RemindersCreateAdapter


class ReminderObservationRecovery:
    def __init__(self,account,store=None,adapter=None):
        self.account=account;self.store=store or PlanStore();self.adapter=adapter or RemindersCreateAdapter()
        self.engine=NativeChanges('reminders',account,self.adapter,self.store)

    def original(self,identity):
        plan=self.store.read(self.store.path(identity,'.json'));result=self.store.read(self.store.path(identity,'.result'))
        items=plan.get('payload',{}).get('changes',[])
        if (plan.get('kind')!='reminders_change' or plan.get('payload',{}).get('account')!=self.account
                or not isinstance(items,list) or len(items)!=1 or result.get('status')!='failed_or_uncertain'
                or result.get('phase')!='mutation' or result.get('attempted')!=[] or result.get('completed')!=[]
                or self.store.read(self.store.path(identity,'.started')).get('sha256')!=digest(plan)
                or list(self.store.root.glob(identity+'.attempt-*'))):
            raise SafetyError('Recovery requires one owned uncertain Reminder mutation with no completed item')
        return plan,result,items[0]

    def observation(self,identity):
        value=inspect_uncertain_reminder(self.engine,identity)
        if (value.get('observed_snapshot_matches_before') is not True or value.get('terminal_response')!='recorded'
                or value.get('helper_reported_mutation_dispatched') is not True):
            raise SafetyError('A matching snapshot and request-bound terminal dispatch report are required')
        response=self.store.read(self.store.path(identity,'.native-reminder-response-0'))
        if response.get('response_kind')!='error':raise SafetyError('Recovery is limited to a recorded terminal helper error')
        return value

    def prepare(self,original_id):
        self.engine.context(True)
        with self.engine.serialized():
            plan,result,item=self.original(original_id)
            if self.store._native_reminder_recovery_completed(original_id):raise SafetyError('An observation has already been accepted for this Reminder')
            observed=self.observation(original_id)
            request=self.store.read(self.store.path(original_id,'.native-reminder-request-0'))
            response=self.store.read(self.store.path(original_id,'.native-reminder-response-0'))
            payload={'account':self.account,'context':self.engine.context(True),'original_id':original_id,
                'original_sha256':digest(plan),'original_result_sha256':digest(result),'original_item_sha256':digest(item),
                'request_record_sha256':digest(request),'response_record_sha256':digest(response),
                'operation':'accept_observed_unchanged_reminder','observed':observed,
                'effects':{'native_data_changed':False,'original_result_unchanged':True,'old_plan_replay':'forbidden',
                    'decision':'Accept only the current observed reminder snapshot after review and allow fresh, separately authorized work.',
                    'uncertainty':'The original dispatch and provider effects remain unproven. Unexposed metadata, other objects and delayed effects are not ruled out.',
                    'future_actions':'Fresh plans still require current scope, fidelity and approval checks.'}}
            return self.store.prepare('reminders_observation_recovery',payload)

    def checked(self,identity,*,pending=False):
        plan=self.store.proposal(identity) if pending else self.store.read(self.store.path(identity,'.json'))
        payload=plan.get('payload',{});original_id=payload.get('original_id')
        if not original_id or not self.store._native_reminder_recovery_for(original_id,plan):raise SafetyError('Invalid Reminder observation binding or an observation was already accepted')
        if payload.get('context')!=self.engine.context(True):raise SafetyError('Reminder observation access, backend or approval policy changed')
        if self.observation(original_id)!=payload.get('observed'):raise SafetyError('Observed Reminder state or terminal evidence changed since review')
        return plan

    def proof(self,plan,result):
        original_id=plan['payload']['original_id'];record={'status':'reviewed_unchanged_reminder_accepted','recovery_id':plan['plan_id'],
            'recovery_result_sha256':digest(result),'original_result_sha256':plan['payload']['original_result_sha256'],'observed_sha256':result['after_sha256']}
        path=self.store.path(original_id,'.native-reminder-recovery-result')
        if path.exists():
            if self.store.read(path)!=record:raise SafetyError('Reminder observation proof conflicts with an earlier association')
        else:self.store.write_once(path,record)

    def execute(self,identity):
        self.engine.context(True)
        with self.engine.serialized():
            plan=self.checked(identity,pending=True);self.engine.assert_settled(plan);self.store.consume(identity,self.account)
            try:
                if self.engine.context(True)!=plan['payload']['context'] or self.observation(plan['payload']['original_id'])!=plan['payload']['observed']:
                    raise SafetyError('Observed Reminder state changed')
            except Exception:
                return self.store.finish(identity,{'status':'completed','recovery_state':'not_accepted','native_data_changed':False,'original_result_unchanged':True,'message':'Original uncertainty remains; prepare a fresh observation after resolving the read failure.'})
            result={'status':'completed','recovery_state':'observed_unchanged_reminder_accepted_after_review','native_data_changed':False,
                    'original_result_unchanged':True,'causal_dispatch_proven':False,'after_sha256':digest(plan['payload']['observed']),'do_not_retry':True}
            self.store.finish(identity,result);self.proof(plan,result);return result

    def finalize(self,identity):
        self.engine.context(True)
        with self.engine.serialized():
            stored=self.store.read(self.store.path(identity,'.json'));original_id=stored.get('payload',{}).get('original_id')
            if stored.get('kind')=='reminders_observation_recovery' and stored['payload'].get('account')==self.account and original_id and self.store._native_reminder_recovery_completed(original_id):
                if self.store.read(self.store.path(original_id,'.native-reminder-recovery-result'))['recovery_id']!=identity:raise SafetyError('Another observation was finalized for this Reminder')
                return self.store.status(original_id)
            plan=self.checked(identity);result=self.store.read(self.store.path(identity,'.result'))
            if (self.store.read(self.store.path(identity,'.started')).get('sha256')!=digest(plan) or result.get('status')!='completed'
                    or result.get('recovery_state')!='observed_unchanged_reminder_accepted_after_review' or result.get('after_sha256')!=digest(plan['payload']['observed'])):
                raise SafetyError('Only a successful admitted Reminder observation can be finalized')
            self.proof(plan,result);return self.store.status(plan['payload']['original_id'])

    def abandon(self,identity):
        from .native_access import access_status
        access_status('reminders')
        with self.engine.serialized():
            plan=self.store.read(self.store.path(identity,'.json'));payload=plan.get('payload',{})
            if plan.get('kind')!='reminders_observation_recovery' or payload.get('account')!=self.account:raise SafetyError('Observation belongs to another owner or operation')
            if self.store.path(payload['original_id'],'.native-reminder-recovery-result').exists():raise SafetyError('A finalized observation cannot be withdrawn')
            marker=self.store.path(identity,'.native-reminder-recovery-abandoned');record={'recovery_sha256':digest(plan),'native_data_changed':False}
            if marker.exists():
                if self.store.read(marker)!=record:raise SafetyError('Reminder observation withdrawal evidence changed')
            else:self.store.write_once(marker,record)
            if not self.store.path(identity,'.started').exists():
                cancelled=self.store.path(identity,'.cancelled')
                if not cancelled.exists():self.store.write_once(cancelled,{'plan_sha256':digest(plan),'cancelled_at':self.store.clock()})
            elif not self.store.path(identity,'.result').exists():self.store.finish(identity,{'status':'completed','recovery_state':'not_accepted','native_data_changed':False,'original_result_unchanged':True})
            return {'status':'abandoned','original_uncertainty_retained':True,'native_data_changed':False}
