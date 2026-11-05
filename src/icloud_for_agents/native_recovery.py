"""Explicit reviewed association of an uncertain single Notes creation.

This does not assert provider-returned identity or replay creation. An authorized client may
accept one matching observed object, or repair an exact title-only candidate.
All original records remain unchanged. Ordinary strict/trusted policy applies.
"""
import uuid
import json
from html import escape

from .approvals import PlanStore,authorization_path
from .core import SafetyError,digest
from .native_changes import NativeChanges
from .native_notes import NotesCreateAdapter
from .native_read import native_config
from .native_protocol import mutation_request


class NotesCreationRecovery:
    def __init__(self,account,store=None,adapter=None):
        self.account=account;self.store=store or PlanStore();self.adapter=adapter or NotesCreateAdapter()
        self.engine=NativeChanges('notes',account,self.adapter,self.store)

    def context(self,plan=None):
        # New recovery reviews bind exact temporary authority. Existing reviews
        # retain their original persistent-mode contract.
        temporary=plan is None or plan.get('payload',{}).get('context',{}).get('temporary_elevation')=='exact-batch-once'
        return self.engine.context(temporary)

    def original(self,plan_id):
        context=self.engine.context(True);plan=self.store.read(self.store.path(plan_id,'.json'))
        result=self.store.read(self.store.path(plan_id,'.result'))
        changes=plan.get('payload',{}).get('changes',[])
        if (plan.get('kind')!='notes_change' or plan['payload'].get('account')!=self.account
                or plan['payload'].get('context',{}).get('source_ids')!=context['source_ids']
                or result.get('status')!='failed_or_uncertain' or result.get('phase')!='mutation'
                or result.get('attempted')!=[] or result.get('completed')!=[]
                or len(changes)!=1 or changes[0].get('operation')!='create_note'):
            raise SafetyError('Recovery is limited to one uncertain Notes creation with no returned identity')
        return plan,result,changes[0]

    def snapshot(self,item,candidate_id):
        folder=self.adapter.private_folder(item['folder_id'],item['source_id'])
        if folder.get('source_id')!=item['source_id'] or folder.get('shared') is not False:
            raise SafetyError('Recovery folder is outside scope or shared')
        note=self.adapter.reader.read('get',folder_id=item['folder_id'],id=candidate_id)['item']
        if (note.get('id')!=candidate_id or note.get('source_id')!=item['source_id']
                or note.get('folder_id')!=item['folder_id'] or note.get('locked') is not False
                or note.get('shared') is not False or note.get('truncated') is not False
                or note.get('title')!=item['fields']['title']):
            raise SafetyError('Exact unlocked, unshared candidate in the original folder is required')
        # Inspect the entire bounded title set: one same-title candidate only.
        titles=[];offset=0
        while True:
            page=self.adapter.reader.read('query',folder_id=item['folder_id'],limit=100,offset=offset)
            titles.extend(page['items'])
            if len(titles)>1000:raise SafetyError('Narrow the recovery folder before review')
            if page.get('done'):break
            following=page.get('next_offset')
            if type(following) is not int or following<=offset:raise SafetyError('Incomplete candidate discovery')
            offset=following
        matching=[n['id'] for n in titles if n.get('title')==item['fields']['title']]
        if matching!=[candidate_id]:raise SafetyError('Candidate association is ambiguous')
        return {'folder':folder,'note':note,'same_title_ids':matching}

    def validate_target(self,item,snapshot,action):
        outcome={'id':snapshot['note']['id'],'source_id':item['source_id'],'folder_id':item['folder_id']}
        if action=='accept_observed_creation':
            if self.adapter.verify(item,outcome) is not True:
                raise SafetyError('Observed note does not satisfy the complete original request')
        elif action=='repair_title_only_candidate':
            note=snapshot['note']
            canonical='<div>'+escape(note['title'],quote=False)+'</div>'
            if note['html'].strip()!=canonical or ' '.join(note.get('text','').split())!=note['title']:
                raise SafetyError('Repair is limited to an exact title-only native HTML candidate')
            attachments=self.adapter.reader.read('attachments',folder_id=item['folder_id'],id=note['id'],limit=1,offset=0)
            if attachments.get('attachments')!=[] or attachments.get('done') is not True or attachments.get('locked'):
                raise SafetyError('Candidate attachments are unavailable or nonempty; no repair')
        else:raise SafetyError('Choose explicit accept_observed_creation or repair_title_only_candidate')

    def prepare(self,original_id,candidate_id,action):
        if not isinstance(candidate_id,str) or not 1<=len(candidate_id)<=1000:raise SafetyError('Select one exact candidate ID')
        with self.engine.serialized():
            original,result,item=self.original(original_id)
            snapshot=self.snapshot(item,candidate_id)
            if action=='repair_title_only_candidate':
                snapshot['folder']=self.adapter.checked_content_folder_role(snapshot['folder'],item['source_id'])
            self.validate_target(item,snapshot,action)
            if action=='repair_title_only_candidate':
                repair={'operation':'repair_note_body','source_id':item['source_id'],'folder_id':item['folder_id'],'id':candidate_id,'fields':item['fields'],'before':snapshot}
                if len(json.dumps(mutation_request('notes',repair),ensure_ascii=False).encode())>239000:raise SafetyError('Recovery exceeds the fixed helper request limit')
            identifier=uuid.uuid4().hex
            plan={'plan_id':identifier,'kind':'notes_recovery','created':self.store.clock(),'expires':self.store.clock()+1800,
                'native_recovery_of':original_id,'payload':{'account':self.account,'context':self.context(),
                'original_sha256':digest(original),'original_result_sha256':digest(result),
                'operation':action,'source_id':item['source_id'],'folder_id':item['folder_id'],
                'candidate_id':candidate_id,'before':snapshot,'requested_fields':item['fields'],
                'effects':{'creation_replayed':False,'identity_provenance':'reviewed association under selected policy; not a provider-returned creation ID',
                    'choice':'Accept one fully matching observed note' if action=='accept_observed_creation' else 'Replace only this exact title-only candidate with the originally requested title/body',
                    'uncertainty':'Original failed result remains unchanged; creation timing/provenance is not established',
                    'unrelated_content':'unchanged','sync_status':'unconfirmed'}}}
            # One reservation per original attempt. A crash never enables two repairs.
            self.store.write_once(self.store.path(original_id,'.native-recovery'),
                {'status':'reserved_reviewed_notes_association','original_sha256':digest(original),
                 'original_result_sha256':digest(result),'recovery_id':identifier,'recovery_sha256':digest(plan)})
            self.store.write_once(self.store.path(identifier,'.json'),plan)
            path,receipt=authorization_path(plan)
            return {'plan_id':identifier,'review':plan,'sha256':digest(plan),'approval_required':True,
                    'authorization_path':path,'local_receipt_required':receipt,
                    'review_tool':'owner_request_review' if receipt else None}

    def execute(self,recovery_id):
        with self.engine.serialized():
            plan=self.store.proposal(recovery_id);original_id=plan.get('native_recovery_of')
            if not original_id or not self.store._native_recovery_for(original_id,plan):raise SafetyError('Invalid recovery reservation')
            original,original_result,item=self.original(original_id);payload=plan['payload']
            if payload.get('account')!=self.account or payload.get('context')!=self.context(plan):
                raise SafetyError('Recovery account, backend or access changed')
            current=self.snapshot(item,payload['candidate_id'])
            if payload['operation']=='repair_title_only_candidate':
                current['folder']=self.adapter.checked_content_folder_role(current['folder'],item['source_id'])
            if current!=payload['before']:raise SafetyError('Candidate changed since review')
            self.validate_target(item,current,payload['operation'])
            self.engine.assert_settled(plan)
            self.store.consume(recovery_id,self.account)
            outcome={'id':payload['candidate_id'],'source_id':item['source_id'],'folder_id':item['folder_id']}
            try:
                if self.context(plan)!=payload['context']:raise SafetyError('Access changed before recovery')
                if payload['operation']=='repair_title_only_candidate':
                    change={'operation':'repair_note_body','source_id':item['source_id'],'folder_id':item['folder_id'],
                            'id':payload['candidate_id'],'fields':item['fields'],'before':current}
                    from .native_once import dispatch
                    from .native_access import require_write
                    # The consumed recovery authorizes only this immutable repair.
                    # No application setting or ambient write mode is changed.
                    with dispatch('notes',change):
                        require_write('notes',change)
                        if self.context(plan)!=payload['context']:raise SafetyError('Recovery access changed before exact repair')
                        outcome=self.adapter.reader._dispatch(mutation_request('notes',change),native_config('notes'))['result']
                    self.store.write_once(self.store.path(recovery_id,'.observed-outcome'),
                        {'recovery_sha256':digest(plan),'result':outcome})
                if self.adapter.verify(item,outcome) is not True:raise SafetyError('Original requested note does not verify after recovery')
                # Recheck uniqueness, source, shared/locked state and exact content.
                after=self.snapshot(item,outcome['id'])
                self.validate_target(item,after,'accept_observed_creation')
                result={'status':'completed','recovery_state':'observed_state_accepted_after_review',
                    'original_result_unchanged':True,'observed_candidate_id':outcome['id'],
                    'identity_provenance':'reviewed association under selected policy; not provider-returned identity or independent human proof',
                    'native_data_changed':payload['operation']=='repair_title_only_candidate','after_sha256':digest(after),'do_not_retry':True}
            except Exception:
                return self.store.finish(recovery_id,{'status':'failed_or_uncertain','do_not_retry':True,
                    'message':'Inspect original and recovery audits; no automatic replay or uncertainty clear'})
            self.store.finish(recovery_id,result)
            self.store.write_once(self.store.path(original_id,'.native-recovery-result'),
                {'status':'reviewed_observed_notes_state_verified','recovery_id':recovery_id,
                 'recovery_result_sha256':digest(result),'original_result_sha256':digest(original_result),
                 'after_sha256':digest(after),'checked_at':self.store.clock()})
            return result

    def finalize(self,recovery_id):
        """Read-only completion of a successful recovery whose final proof was interrupted."""
        with self.engine.serialized():
            plan=self.store.read(self.store.path(recovery_id,'.json'));original_id=plan.get('native_recovery_of')
            if not original_id or not self.store._native_recovery_for(original_id,plan):raise SafetyError('Invalid recovery reservation')
            if plan['payload'].get('account')!=self.account or plan['payload'].get('context')!=self.context(plan):
                raise SafetyError('Recovery account, backend or access changed')
            if self.store._native_recovery_completed(original_id):return self.store.status(original_id)
            original,original_result,item=self.original(original_id);result=self.store.read(self.store.path(recovery_id,'.result'))
            if result.get('status')!='completed' or result.get('recovery_state')!='observed_state_accepted_after_review':
                raise SafetyError('Only a successful recovery may finalize its proof')
            candidate=result.get('observed_candidate_id')
            if plan['payload']['operation']=='accept_observed_creation':
                if candidate!=plan['payload']['candidate_id']:raise SafetyError('Associated identity changed')
            else:
                observed=self.store.read(self.store.path(recovery_id,'.observed-outcome'))
                if observed.get('recovery_sha256')!=digest(plan) or observed.get('result',{}).get('id')!=candidate:
                    raise SafetyError('Exact returned repair identity required')
            after=self.snapshot(item,candidate);self.validate_target(item,after,'accept_observed_creation')
            if digest(after)!=result.get('after_sha256'):raise SafetyError('Observed state changed after recovery')
            self.store.write_once(self.store.path(original_id,'.native-recovery-result'),
                {'status':'reviewed_observed_notes_state_verified','recovery_id':recovery_id,
                 'recovery_result_sha256':digest(result),'original_result_sha256':digest(original_result),
                 'after_sha256':digest(after),'checked_at':self.store.clock()})
            return self.store.status(original_id)
