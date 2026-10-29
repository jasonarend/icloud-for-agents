"""Reviewed observation of one uncertain, pre-existing Notes move target.

No native mutation, causal dispatch claim, original audit rewrite or plan replay.
The exact known ID must retain its observable content/attachments in exactly one
of the two reviewed folders, with a bound terminal helper response available.
"""
from .approvals import PlanStore
from .core import SafetyError,digest
from .native_changes import NativeChanges
from .native_notes import NotesCreateAdapter
from .native_protocol import mutation_request
from .native_app import completed_exchange_evidence

CONTENT_KEYS=('id','title','html','text','created','locked','shared','truncated')

class NotesMoveRecovery:
    def __init__(self,account,store=None,adapter=None):
        self.account=account;self.store=store or PlanStore();self.adapter=adapter or NotesCreateAdapter()
        self.engine=NativeChanges('notes',account,self.adapter,self.store)
    def original(self,identity):
        context=self.engine.context(True);plan=self.store.read(self.store.path(identity,'.json'));result=self.store.read(self.store.path(identity,'.result'));items=plan.get('payload',{}).get('changes',[])
        if (plan.get('kind')!='notes_change' or plan['payload'].get('account')!=self.account
                or plan['payload'].get('context',{}).get('source_ids')!=context['source_ids']
                or result.get('status')!='failed_or_uncertain' or result.get('phase') not in {'mutation','verification'}
                or len(items)!=1 or items[0].get('operation') not in {'move_note','restore_note','delete_note'} or items[0].get('id')!=items[0].get('before',{}).get('note',{}).get('id')
                or items[0].get('source_id')!=items[0].get('before',{}).get('note',{}).get('source_id')
                or items[0].get('folder_id')!=items[0].get('before',{}).get('note',{}).get('folder_id')
                or result.get('completed')!=[] or not isinstance(result.get('attempted'),list) or len(result['attempted'])>1
                or self.store.read(self.store.path(identity,'.started')).get('sha256')!=digest(plan)):
            raise SafetyError('Observation recovery covers only one owned uncertain move of an exact pre-existing note ID')
        attempts=result['attempted'];expected=set()
        if attempts:
            attempt=attempts[0];expected={self.store.path(identity,'.attempt-0')}
            if (attempt.get('index')!=0 or attempt.get('operation')!=items[0]['operation'] or attempt.get('result',{}).get('id')!=items[0]['id']
                    or self.store.read(self.store.path(identity,'.attempt-0'))!={'plan_sha256':digest(plan),'attempt':attempt}):raise SafetyError('Known move attempt evidence is inconsistent')
        if set(self.store.root.glob(identity+'.attempt-*'))!=expected:raise SafetyError('Unexpected move dispatch evidence; no observation association')
        return plan,result,items[0]
    def snapshot(self,item):
        source=item['folder_id'];destination=item['fields']['destination_folder_id'];identity=item['id']
        if item['operation'] in {'restore_note','delete_note'}:
            if item['operation']=='restore_note':from .native_notes_restore import observe
            else:from .native_notes_delete import observe
            observed,physical,roles=observe(self.adapter,item);note=observed['note'];actual=physical['folder_id'];before=item['before']['note']
            return {'note_id':identity,'source_id':item['source_id'],'source_folder_id':source,'destination_folder_id':destination,
                    'observed_folder_id':actual,'location':'source' if actual==source else 'destination','title':note['title'],
                    'original_modified':before.get('modified'),'observed_modified':note.get('modified'),
                    'content_sha256':digest({key:note.get(key) for key in CONTENT_KEYS}),'attachments_sha256':digest(observed['attachments']),
                    'folders':{source:item['before']['folder'],destination:item['before']['destination']},
                    'persisted_location':physical,'recovery_roles':roles}
        folders={key:self.adapter.private_folder(key,item['source_id']) for key in (source,destination)}
        present={key:self.adapter.reader.read('contains',folder_id=key,id=identity).get('exists') for key in (source,destination)}
        if any(type(v) is not bool for v in present.values()) or sum(present.values())!=1:
            raise SafetyError('Known move object must be physically present in exactly one reviewed folder')
        actual=next(key for key,value in present.items() if value);candidate=dict(item,folder_id=actual)
        observed=self.adapter.note_snapshot(candidate,rewrite=False,verify_folder_role=False);note=observed['note'];before=item['before']['note']
        if (note.get('source_id')!=item['source_id'] or note.get('folder_id')!=actual
                or any(note.get(key)!=before.get(key) for key in CONTENT_KEYS) or observed['attachments']!=item['before']['attachments']):
            raise SafetyError('Known move content, identity or attachment state changed; no recovery association')
        return {'note_id':identity,'source_id':item['source_id'],'source_folder_id':source,'destination_folder_id':destination,
                'observed_folder_id':actual,'location':'source' if actual==source else 'destination','title':note['title'],
                'original_modified':before.get('modified'),'observed_modified':note.get('modified'),
                'content_sha256':digest({key:note.get(key) for key in CONTENT_KEYS}),
                'attachments_sha256':digest(observed['attachments']),'folders':folders}
    def prepare(self,original_id):
        self.engine.context(True)
        with self.engine.serialized():
            original,result,item=self.original(original_id)
            if self.store._native_move_recovery_completed(original_id):raise SafetyError('Known move observation has already been accepted')
            snapshot=self.snapshot(item);exchange=completed_exchange_evidence(mutation_request('notes',item),plan_id=original_id,store=self.store)
            return self.store.prepare('notes_move_recovery',{'account':self.account,'context':self.engine.context(True),
                'original_id':original_id,'original_sha256':digest(original),'original_result_sha256':digest(result),
                'original_item_sha256':digest(item),'original_context':original['payload']['context'],
                'operation':'accept_observed_known_move_state','observed':snapshot,'terminal_exchange':exchange,
                'effects':{'native_data_changed':False,'original_result_unchanged':True,'old_plan_replay':'forbidden',
                    'decision':'Accept this known native object and its current source/destination location under the selected approval policy.',
                    'uncertainty':'This does not prove whether or when the original move was dispatched. Native completion timing and causal provenance remain unproven.',
                    'future_actions':'Fresh plans retain current location, content and permission checks; no mutation is retried by recovery.'}})
    def checked(self,identity,*,pending=False):
        plan=self.store.proposal(identity) if pending else self.store.read(self.store.path(identity,'.json'));payload=plan.get('payload',{});original_id=payload.get('original_id')
        if not original_id or not self.store._native_move_recovery_for(original_id,plan):raise SafetyError('Invalid known-move recovery binding or a prior accepted observation exists')
        if payload.get('context')!=self.engine.context(True):raise SafetyError('Known-move recovery access, backend or policy changed')
        original,result,item=self.original(original_id)
        if (self.snapshot(item)!=payload.get('observed') or completed_exchange_evidence(mutation_request('notes',item),plan_id=original_id,store=self.store)!=payload.get('terminal_exchange')):
            raise SafetyError('Known move state or terminal exchange changed since review')
        return plan,original,result,item
    def proof(self,plan,result):
        original_id=plan['payload']['original_id'];record={'status':'reviewed_known_move_state_accepted','recovery_id':plan['plan_id'],
             'recovery_result_sha256':digest(result),'original_result_sha256':plan['payload']['original_result_sha256'],
             'observed_sha256':result['after_sha256']};path=self.store.path(original_id,'.native-move-recovery-result')
        if path.exists():
            if self.store.read(path)!=record:raise SafetyError('Known-move observation proof conflicts with an earlier association')
        else:self.store.write_once(path,record)
    def execute(self,identity):
        self.engine.context(True)
        with self.engine.serialized():
            plan,original,original_result,item=self.checked(identity,pending=True)
            self.engine.assert_settled(plan);self.store.consume(identity,self.account)
            try:
                if self.engine.context(True)!=plan['payload']['context'] or self.snapshot(item)!=plan['payload']['observed']:
                    raise SafetyError('Observed move state changed at acceptance')
            except Exception:
                return self.store.finish(identity,{'status':'completed','recovery_state':'not_accepted','native_data_changed':False,
                    'original_result_unchanged':True,'message':'The original uncertainty remains. Prepare a fresh reviewed observation after resolving the read/state failure.'})
            result={'status':'completed','recovery_state':'observed_known_move_state_accepted_after_review',
                    'native_data_changed':False,'original_result_unchanged':True,'causal_dispatch_proven':False,
                    'location':plan['payload']['observed']['location'],'after_sha256':digest(plan['payload']['observed']),'do_not_retry':True}
            self.store.finish(identity,result);self.proof(plan,result);return result
    def finalize(self,identity):
        self.engine.context(True)
        with self.engine.serialized():
            stored=self.store.read(self.store.path(identity,'.json'));original_id=stored.get('payload',{}).get('original_id')
            if stored.get('kind')=='notes_move_recovery' and stored['payload'].get('account')==self.account and original_id and self.store._native_move_recovery_completed(original_id):
                if self.store.read(self.store.path(original_id,'.native-move-recovery-result'))['recovery_id']!=identity:raise SafetyError('Another observation was finalized for this move')
                return self.store.status(original_id)
            plan,original,original_result,item=self.checked(identity);result=self.store.read(self.store.path(identity,'.result'))
            if (self.store.read(self.store.path(identity,'.started')).get('sha256')!=digest(plan)
                    or result.get('status')!='completed' or result.get('recovery_state')!='observed_known_move_state_accepted_after_review'
                    or result.get('after_sha256')!=digest(plan['payload']['observed'])):
                raise SafetyError('Only a successful admitted known-move observation can finalize its proof')
            self.proof(plan,result);return self.store.status(plan['payload']['original_id'])
    def abandon(self,identity):
        from .native_access import access_status
        access_status('notes')
        with self.engine.serialized():
            plan=self.store.read(self.store.path(identity,'.json'));payload=plan.get('payload',{})
            if plan.get('kind')!='notes_move_recovery' or payload.get('account')!=self.account:raise SafetyError('Observation belongs to another owner or operation')
            if self.store.path(payload['original_id'],'.native-move-recovery-result').exists():raise SafetyError('A finalized observation cannot be recalled; inspect subsequent native outcomes')
            marker=self.store.path(identity,'.native-move-recovery-abandoned');record={'recovery_sha256':digest(plan),'native_data_changed':False}
            if marker.exists():
                if self.store.read(marker)!=record:raise SafetyError('Observation withdrawal evidence changed')
            else:self.store.write_once(marker,record)
            if not self.store.path(identity,'.started').exists():
                cancelled=self.store.path(identity,'.cancelled')
                if not cancelled.exists():self.store.write_once(cancelled,{'plan_sha256':digest(plan),'cancelled_at':self.store.clock()})
            elif not self.store.path(identity,'.result').exists():
                self.store.finish(identity,{'status':'completed','recovery_state':'not_accepted','native_data_changed':False,'original_result_unchanged':True})
            return {'status':'abandoned','original_uncertainty_retained':True,'native_data_changed':False}
