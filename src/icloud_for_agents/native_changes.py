"""Shared native approval executor. Concrete write adapters are gated separately.

No native write adapter is registered by this module. Adapters must implement fixed
operations, preserve unrelated native data, and provide independent readback.
"""
from contextlib import contextmanager, nullcontext
import fcntl
import os
import json
import stat

from .core import SafetyError, NativeOperationRefused, canonical, digest
from .native_access import require_write
from .native_protocol import mutation_request


class NativeChanges:
    def __init__(self, name, account, adapter, store):
        if name not in {'notes', 'reminders'}:
            raise SafetyError('Unknown native service')
        self.name, self.account, self.adapter, self.store = name, account, adapter, store
        self.kind = name + '_change'

    def context(self,temporary=False):
        from .native_once import selected_read_access
        status = selected_read_access(self.name) if temporary else require_write(self.name)
        context={'service':self.name,'source_ids':sorted(status['source_ids']),
                'backend':self.adapter.backend_id,'approval_policy':status['approval_policy']}
        if temporary:context.update(temporary_elevation='exact-batch-once',configured_access=status['configured_access'],runtime_access=status['runtime_access'])
        return context

    def prepare_once(self,changes,*,_review_context=None):
        from .native_once import planning
        with planning(self.name):return self.prepare(changes,_temporary=True,_review_context=_review_context)

    def prepare_with_grant(self,grant_id,changes,*,_review_context=None):
        from .native_grants import NativeGrants
        from .native_once import planning
        manager=NativeGrants(self);grant=manager.checked(grant_id)
        with planning(self.name):return self.prepare(changes,_temporary=True,_grant=grant,_review_context=_review_context)

    def prepare(self, changes, *, _temporary=False,_grant=None,_review_context=None):
        context = self.context(_temporary)
        if _review_context is not None:
            expected={'reminders':'create_lossy_copies_from_user_supplied_observation','notes':'create_lossy_note_copy_from_user_supplied_observation'}
            if (not _temporary or not isinstance(_review_context,dict) or _review_context.get('operation')!=expected[self.name]):
                raise SafetyError('Invalid scoped copy review')
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
            if _grant is not None:
                from .native_grants import NativeGrants
                item['requested_fields']=sorted(change['fields'])
                if _review_context is not None:item['standing_operation']='copy_observation'
                NativeGrants(self).match(_grant,item)
            self.check_item(item,context)
            identity = item.get('id')
            if identity:
                if identity in seen:
                    raise SafetyError('Repeated target in one batch; prepare sequential changes separately')
                seen.add(identity)
            prepared.append(item)
        payload = {'account':self.account,'context':context,'changes':prepared,
                   'transaction':'per-item; no atomic backend transaction or automatic rollback'}
        if _review_context is not None:
            payload['copy_review']=_review_context
        if _grant is not None:
            from .native_grants import NativeGrants
            payload['standing_grant']={'grant_id':_grant['plan_id'],'sha256':digest(_grant)}
            NativeGrants(self).check_batch({'payload':payload},_grant)
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
        if len(json.dumps(mutation_request(self.name,item),ensure_ascii=False).encode())>(239000 if self.name=='notes' else 65000):
            raise SafetyError('Native operation exceeds the fixed request limit; narrow the content or attachment scope')

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
                if not (self.store._reconciliation_completed(started.stem) or (correction and correction.get('kind')=='notes_move_recovery' and self.store._native_move_recovery_for(started.stem,correction)) or (correction and correction.get('kind')=='notes_edit_recovery' and self.store._native_edit_recovery_for(started.stem,correction)) or (correction and correction.get('kind')=='reminders_observation_recovery' and self.store._native_reminder_recovery_for(started.stem,correction)) or (correction and correction.get('native_recovery_of')==started.stem and self.store._native_recovery_for(started.stem,correction)) or (correction and correction.get('native_correction_of')==started.stem and self.store._native_correction_for(started.stem,correction))):
                    raise SafetyError('Prior write unfinished or uncertain; reconcile before another native write')

    def prepare_update_correction(self,original_id,target="requested"):
        """Propose one explicit same-object correction; never retry an unknown mutation.

        One fully identified reminder update or list-color change with failed readback qualifies.
        Current state and the chosen requested/before state remain reviewable.
        The ordinary strict/trusted authorization applies to the new plan.
        """
        if self.name!='reminders':raise SafetyError('Correction is limited to known Reminders changes')
        import uuid
        if target not in {'requested','before'}:raise SafetyError('Correction target must be requested or before')
        context=self.context(True)
        with self.serialized():
            original=self.store.read(self.store.path(original_id,'.json'));result=self.store.read(self.store.path(original_id,'.result'))
            changes=original.get('payload',{}).get('changes',[]);attempts=result.get('attempted',[])
            if (original.get('kind')!=self.kind or original.get('native_correction_of')
                    or original['payload'].get('account')!=self.account
                    or original['payload'].get('context',{}).get('source_ids')!=context['source_ids']
                    or result.get('status')!='failed_or_uncertain' or result.get('phase')!='verification'
                    or len(changes)!=1 or len(attempts)!=1 or changes[0].get('operation') not in {'update_reminder','set_recurrence','set_list_color','set_flagged','set_urgent'}
                    or self.store.read(self.store.path(original_id,'.started')).get('sha256')!=digest(original)):
                raise SafetyError('Only one complete known-ID Reminders change can be corrected')
            item=changes[0];attempt=attempts[0]
            if (attempt.get('index')!=0 or attempt.get('operation')!=item['operation']
                    or attempt.get('result',{}).get('id')!=item.get('id')
                    or self.store.read(self.store.path(original_id,'.attempt-0'))!={'plan_sha256':digest(original),'attempt':attempt}):
                raise SafetyError('Exact immutable returned identity required')
            if item['operation']=='set_list_color':
                baseline=item['before'];current=self.adapter.list_rename_snapshot(item)
                if ({k:v for k,v in current['list'].items() if k!='color'}!={k:v for k,v in baseline['list'].items() if k!='color'} or current['siblings']!=baseline['siblings'] or current['membership']!=baseline['membership']):raise SafetyError('Color correction cannot overwrite changed list metadata or membership')
                color=baseline['list']['color'] if target=='before' else item['fields']['color']
                from .native_once import planning
                with planning(self.name):repair=self.adapter.prepare_list_color({'operation':'set_list_color','source_id':item['source_id'],'list_id':item['list_id'],'fields':{'color':color}},allow_noop=True)
            else:
                expected=dict(item['before']['item']);expected.update(item['fields'])
                if 'alarms' in item['fields']:expected['has_alarms']=bool(item['fields']['alarms'])
                current=self.adapter.reader.read('get',id=item['id'])['item']
                observed_native=self.adapter.native_snapshot(current);baseline_native=item['before'].get('native_metadata')
                if observed_native is not None and (type(item['before'].get('urgent')) is not bool or (item['operation']!='set_urgent' and self.adapter.urgent_snapshot(current) is not item['before']['urgent'])):raise SafetyError('Urgent state changed or was not reviewed; correction is unavailable')
                if item['operation']=='set_urgent':
                    if not isinstance(observed_native,dict) or observed_native!=baseline_native:raise SafetyError('Unrelated native metadata changed; urgent correction is unavailable')
                    baseline=item['before']['item']
                    for key in ('id','source_id','list_id','title','notes','priority','due','start','completed','completion_date','url','location','created','alarms','has_alarms','has_recurrence','recurrence_rules'):
                        if current.get(key)!=baseline.get(key):raise SafetyError('Urgent correction cannot overwrite unrelated changed fields')
                    desired=item['before']['urgent'] if target=='before' else item['fields']['urgent']
                    from .native_once import planning
                    with planning(self.name):repair=self.adapter.prepare({'operation':'set_urgent','source_id':item['source_id'],'id':item['id'],'fields':{'urgent':desired}})
                elif item['operation']=='set_flagged':
                    if (not isinstance(observed_native,dict) or not isinstance(baseline_native,dict)
                            or {k:v for k,v in observed_native.items() if k not in {'flagged','flag_state'}}!={k:v for k,v in baseline_native.items() if k not in {'flagged','flag_state'}}):
                        raise SafetyError('Unrelated native metadata changed; flag correction cannot overwrite or accept that effect')
                    baseline=item['before']['item']
                    for key in ('id','source_id','list_id','title','notes','priority','due','start','completed','completion_date','url','location','created','alarms','has_alarms','has_recurrence','recurrence_rules'):
                        if current.get(key)!=baseline.get(key):raise SafetyError('Flag correction cannot overwrite unrelated changed fields')
                    desired=baseline_native['flagged'] if target=='before' else item['fields']['flagged']
                    from .native_once import planning
                    with planning(self.name):repair=self.adapter.prepare({'operation':'set_flagged','source_id':item['source_id'],'id':item['id'],'fields':{'flagged':desired}})
                elif observed_native!=baseline_native:raise SafetyError('Observed native metadata changed; public-field correction cannot overwrite or accept that effect')
                elif item['operation']=='set_recurrence':
                    from .native_recurrence import observed_rule
                    baseline=item['before']['item']
                    for key in ('id','source_id','list_id','title','notes','priority','due','start','completed','url','location','created','alarms','has_alarms'):
                        if current.get(key)!=baseline.get(key):raise SafetyError('Recurrence correction cannot overwrite unrelated changed fields')
                    rules=baseline.get('recurrence_rules',[])
                    rule=(observed_rule(rules[0]) if rules else None) if target=='before' else item['fields']['recurrence']
                    from .native_once import planning
                    with planning(self.name):repair=self.adapter.prepare({'operation':'set_recurrence','source_id':item['source_id'],'id':item['id'],
                        'fields':{'recurrence_scope':'exact_native_rule','recurrence':rule}})
                else:
                    for key in ('source_id','list_id','completed','url','has_recurrence','has_alarms'):
                        if current.get(key)!=expected.get(key):raise SafetyError('Unrelated effects or external changes require separate review')
                    desired=item['before']['item'] if target=='before' else expected
                    if target=='before':
                        # Unrequested public fields must retain their original state;
                        # no rollback may erase an external edit or unrelated effect.
                        for key in ('id','source_id','list_id','title','notes','priority','completed','url','location','created','has_recurrence','has_alarms','recurrence_rules','alarms'):
                            if key not in item['fields'] and not (key=='has_alarms' and 'alarms' in item['fields']) and current.get(key)!=item['before']['item'].get(key):raise SafetyError('Unrequested state changed; rollback cannot overwrite it')
                    fields={key:desired.get(key) for key in ('title','notes','priority','due','start','url')}
                    if 'alarms' in item['fields']:
                        from .native_reminders import public_supported_alarms
                        fields['alarms']=public_supported_alarms(item['before']['item'].get('alarms',[])) if target=='before' else item['fields']['alarms']
                    for key in ('due','start'):
                        if isinstance(fields[key],dict):fields[key]={k:v for k,v in fields[key].items() if k in {'year','month','day','hour','minute','timezone'} and v is not None}
                    from .native_once import planning
                    with planning(self.name):repair=self.adapter.prepare({'operation':'update_reminder','source_id':item['source_id'],'id':item['id'],'fields':fields})
            self.check_item(repair,context)
            correction_id=uuid.uuid4().hex
            correction={'plan_id':correction_id,'kind':self.kind,'created':self.store.clock(),'expires':self.store.clock()+1800,
                'native_correction_of':original_id,'payload':{'account':self.account,'context':context,'changes':[repair],
                'original_failure_sha256':digest(result),'correction_target':target,
                'purpose':('Restore the exact original public fields; the failed request remains unsuccessful' if target=='before' else 'Restore the originally requested public fields')+' on the exact returned native object; no creation or replay'}}
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

    def prepare_folder_rename_correction(self,original_id,target='before'):
        if self.name!='notes' or target not in {'requested','before'}:raise SafetyError('Known Notes folder-title correction required')
        import uuid
        context=self.context(True)
        with self.serialized():
            original=self.store.read(self.store.path(original_id,'.json'));result=self.store.read(self.store.path(original_id,'.result'));items=original.get('payload',{}).get('changes',[]);attempts=result.get('attempted',[])
            if (original.get('kind')!='notes_change' or original.get('native_correction_of') or original['payload'].get('account')!=self.account
                    or original['payload'].get('context',{}).get('source_ids')!=context['source_ids'] or result.get('status')!='failed_or_uncertain' or result.get('phase')!='verification'
                    or len(items)!=1 or items[0].get('operation')!='rename_folder' or len(attempts)!=1 or attempts[0].get('index')!=0 or attempts[0].get('operation')!='rename_folder'
                    or attempts[0].get('result',{}).get('id')!=items[0].get('id') or self.store.read(self.store.path(original_id,'.started')).get('sha256')!=digest(original)
                    or self.store.read(self.store.path(original_id,'.attempt-0'))!={'plan_sha256':digest(original),'attempt':attempts[0]}):raise SafetyError('Only one admitted returned-ID folder rename with failed verification qualifies')
            repair=self.adapter.prepare_folder_rename_repair(items[0],target);self.check_item(repair,context);identity=uuid.uuid4().hex
            plan={'plan_id':identity,'kind':'notes_change','created':self.store.clock(),'expires':self.store.clock()+1800,'native_correction_of':original_id,
                'payload':{'account':self.account,'context':context,'changes':[repair],'correction_target':target,'original_failure_sha256':digest(result),
                    'purpose':'Explicit exact folder-title correction; no rename replay, content rewrite or generic uncertainty clear'}}
            self.store.write_once(self.store.path(original_id,'.native-correction'),{'status':'reserved_known_update_correction','original_sha256':digest(original),'original_result_sha256':digest(result),'correction_id':identity,'correction_sha256':digest(plan)})
            self.store.write_once(self.store.path(identity,'.json'),plan)
            from .approvals import authorization_path
            path,receipt=authorization_path(plan)
            return {'plan_id':identity,'review':plan,'sha256':digest(plan),'approval_required':True,'authorization_path':path,'local_receipt_required':receipt}

    def reconcile_correction(self,correction_id):
        """Verify the explicitly chosen requested/before outcome; preserve both audits."""
        self.context(True)
        with self.serialized():
            correction=self.store.read(self.store.path(correction_id,'.json'));original_id=correction.get('native_correction_of')
            if not original_id or not self.store._native_correction_for(original_id,correction):raise SafetyError('Invalid correction reservation')
            if correction['payload'].get('account')!=self.account or correction['payload'].get('context')!=self.context(correction['payload'].get('context',{}).get('temporary_elevation')=='exact-batch-once'):raise SafetyError('Correction scope or backend changed')
            result=self.store.read(self.store.path(correction_id,'.result'))
            original=self.store.read(self.store.path(original_id,'.json'));original_result=self.store.read(self.store.path(original_id,'.result'))
            intended=original['payload']['changes'][0]
            rollback=correction['payload'].get('correction_target')=='before'
            if rollback and intended['operation']=='rename_folder':
                intended=dict(intended,fields={'title':intended['before']['folder']['title']})
            elif rollback and intended['operation']=='set_list_color':
                intended=dict(intended,fields={'color':intended['before']['list']['color']})
            elif rollback and intended['operation']=='set_recurrence':
                from .native_recurrence import observed_rule
                rules=intended['before']['item'].get('recurrence_rules',[])
                intended=dict(intended,fields={'recurrence_scope':'exact_native_rule','recurrence':observed_rule(rules[0]) if rules else None})
            elif rollback and intended['operation']=='set_urgent':
                intended=dict(intended,fields={'urgent':intended['before']['urgent']})
            elif rollback and intended['operation']=='set_flagged':
                intended=dict(intended,fields={'flagged':intended['before']['native_metadata']['flagged']})
            elif rollback:
                fields={key:intended['before']['item'].get(key) for key in ('title','notes','priority','due','start','url')}
                if 'alarms' in intended['fields']:
                    from .native_reminders import public_supported_alarms
                    fields['alarms']=public_supported_alarms(intended['before']['item'].get('alarms',[]))
                intended=dict(intended,fields=fields)
            if (result.get('status')!='applied' or not self.adapter.verify(intended,original_result['attempted'][0]['result'])):
                raise SafetyError('Original requested state still does not verify; uncertainty retained')
            self.store.write_once(self.store.path(original_id,'.native-correction-result'),
                {'status':'original_before_state_restored_after_correction' if rollback else 'original_requested_fields_verified_after_correction','correction_id':correction_id,
                 'correction_result_sha256':digest(result),'original_result_sha256':digest(original_result),'checked_at':self.store.clock()})
            return self.store.status(original_id)

    def reconcile_created(self,plan_id):
        """Local reviewed read-only reconciliation of known returned creation IDs.

        No retry, generic uncertainty clear, model-supplied proof or audit rewrite.
        Other failure phases and incomplete batches remain blocked.
        """
        return self._reconcile_known(plan_id,{'create_note','create_folder','create_reminder','create_list','create_subtask','create_section'})

    def reconcile_known_notes(self,plan_id):
        """Read-only verification of complete returned-ID Notes edits and moves."""
        if self.name!='notes':raise SafetyError('Known edit reconciliation is limited to Notes')
        return self.reconcile_known_changes(plan_id)

    def reconcile_known_changes(self,plan_id):
        allowed=({'append_note','prepend_note','replace_text','update_note','move_note','restore_note','rename_folder'} if self.name=='notes' else {'update_reminder','set_completed','move_reminder','rename_list','set_list_color','set_list_pinned','set_recurrence','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early','rename_section'})
        return self._reconcile_known(plan_id,allowed)

    def _reconcile_known(self,plan_id,allowed):
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
            if (result.get('status')!='failed_or_uncertain' or result.get('phase')!='verification'
                    or not items or len(attempts)!=len(items) or any(i.get('operation') not in allowed for i in items)
                    or self.store.read(self.store.path(plan_id,'.started')).get('sha256')!=digest(plan)):
                raise SafetyError('Only complete allowed known-ID batches with failed readback can be reconciled')
            hashes=[]
            for index,(item,attempt) in enumerate(zip(items,attempts)):
                entry=self.store.read(self.store.path(plan_id,'.attempt-'+str(index)))
                if (entry!={'plan_sha256':digest(plan),'attempt':attempt} or attempt.get('index')!=index
                        or attempt.get('operation')!=item['operation']
                        or (item['operation'] in {'append_note','prepend_note','replace_text','update_note','move_note','restore_note','rename_folder','update_reminder','set_completed','move_reminder','rename_list','set_list_color','set_list_pinned','set_recurrence','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early'} and attempt.get('result',{}).get('id')!=item.get('id'))
                        or (item['operation']=='rename_section' and attempt.get('result',{}).get('id')!=item.get('section_id'))
                        or not self.adapter.verify(item,attempt['result'])):
                    raise SafetyError('Known native object does not verify; uncertainty retained')
                hashes.append(digest(entry))
            self.store.write_once(self.store.path(plan_id,'.native-reconciled'),
                {'status':'native_readback_verified','plan_sha256':digest(plan),'result_sha256':digest(result),
                 'attempts_sha256':digest(attempts),'attempt_record_hashes':hashes,'checked_at':self.store.clock(),
                 'method':'independent adapter readback of exact returned native identities; no mutation or human receipt'})
            return self.store.status(plan_id)

    def finalize_stopped(self,plan_id):
        from .native_once import selected_read_access
        selected_read_access(self.name)
        with self.serialized():
            plan=self.store.read(self.store.path(plan_id,'.json'));result=self.store.read(self.store.path(plan_id,'.result'))
            if plan.get('kind')!=self.kind or plan['payload'].get('account')!=self.account or result.get('status')!='failed_or_uncertain':
                raise SafetyError('Only an owned failed native plan can receive a pre-dispatch audit finalizer')
            proof=self.store.native_predispatch_proof(plan_id,result)
            path=self.store.path(plan_id,'.native-stopped');record={'original_result_sha256':digest(result),'proof':proof,'status':'stopped_before_dispatch_finalized'}
            if path.exists():
                if self.store.read(path)!=record:raise SafetyError('Native stop finalization changed')
            else:self.store.write_once(path,record)
            return self.store.status(plan_id)

    def cancel(self,plan_id):
        from .native_once import selected_read_access
        selected_read_access(self.name)
        with self.serialized():
            plan=self.store.read(self.store.path(plan_id,'.json'))
            if plan.get('kind')!=self.kind or plan.get('payload',{}).get('account')!=self.account:
                raise SafetyError('Cancellation belongs to another service or owner')
            if self.store.path(plan_id,'.started').exists():
                raise SafetyError('A started native action cannot be recalled; inspect its outcome')
            path=self.store.path(plan_id,'.cancelled')
            if not path.exists():self.store.write_once(path,{'plan_sha256':digest(plan),'cancelled_at':self.store.clock()})
            return self.store.status(plan_id)

    def execute(self,plan_id):
        from .native_once import selected_read_access
        selected_read_access(self.name)
        with self.serialized():
            candidate=self.store.proposal(plan_id)
            temporary=candidate.get('payload',{}).get('context',{}).get('temporary_elevation')=='exact-batch-once'
            context=self.context(temporary)
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
            from .native_grants import NativeGrants
            standing=payload.get('standing_grant')
            manager=NativeGrants(self) if standing is not None else None
            if manager:
                grant=manager.checked(standing.get('grant_id'));manager.check_batch(candidate,grant)
            plan=manager.consume(plan_id) if manager else self.store.consume(plan_id,self.account)
            completed=[]
            attempted=[]
            phase='pre_dispatch'
            try:
                for index,item in enumerate(plan['payload']['changes']):
                    phase='pre_dispatch'
                    if self.context(temporary)!=context:
                        raise SafetyError('Native access changed between items')
                    phase='snapshot_recheck'
                    if self.adapter.snapshot(item)!=item.get('before'):
                        raise SafetyError('Native object changed immediately before dispatch')
                    if self.context(temporary)!=context:
                        raise SafetyError('Native access changed during the final snapshot check')
                    if manager:
                        grant=manager.checked(standing['grant_id']);manager.match(grant,item)
                    from .native_once import dispatch
                    from .native_execution import capture
                    with capture(self.store,self.name,plan,index),dispatch(self.name,item) if temporary else nullcontext():
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
                if phase in {'pre_dispatch','snapshot_recheck'} and attempted==completed:
                    proof=self.store.native_predispatch_proof(plan_id,result)
                    result.update(status='stopped_before_dispatch',pre_dispatch_evidence=proof,do_not_retry=True,
                                  message='This item was not dispatched; earlier verified items are listed. The plan remains consumed. Prepare a new reviewed batch after resolving the read or access failure.')
                expected_request=mutation_request(self.name,item)
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
