"""Reviewed, revocable native scenario authority with exact selectors and audit.

An active grant substitutes for per-batch approval only inside its reviewed scope.
It never enables services, changes configuration, approves OS consent or bypasses
adapter fidelity checks. Private files are a same-owner workflow boundary.
"""
from contextlib import contextmanager
import fcntl
import math
import os
import stat
from .core import SafetyError,digest
from .native_once import selected_read_access
from .native_read import native_config

FIELDS={
 'notes':{'copy_observation':{'title','body','format'},'create_note':{'title','body','format'},'append_note':{'body','format'},'prepend_note':{'body','format'},'replace_text':{'find','replacement','expected_matches'},
          'update_note':{'title','body','format'},'move_note':{'destination_folder_id'},'create_folder':{'title'},'rename_folder':{'title'}},
 'reminders':{'copy_observation':{'title','notes','priority','due','start','url','alarms'},'create_subtask':{'title'},'create_section':{'title'},'rename_section':{'title'},'create_reminder':{'title','notes','priority','due','start','url','alarms','location_alarms','recurrence'},'update_reminder':{'title','notes','priority','due','start','url','alarms','location_alarms'},
              'set_completed':{'completed'},'set_flagged':{'flagged'},'set_urgent':{'urgent'},'add_early':{'unit','count'},'clear_early':{'clear'},'remove_tag':{'tag_id'},'add_tag':{'tag_name'},'replace_tag':{'tag_id','tag_name'},'move_reminder':{'list_id'},'create_list':{'title'},'rename_list':{'title'},'set_list_color':{'color'},'set_recurrence':{'recurrence','recurrence_scope'}}}


def exact(value,limit=1000):
    return isinstance(value,str) and 1<=len(value)<=limit and value!='*' and not any(ord(c)<32 for c in value)


def validate_scope(name,scope,now):
    if not isinstance(scope,dict) or set(scope)!={'scenario','rules','max_batch','max_24h','expires'}:
        raise SafetyError('Use an exact native grant schema')
    if not exact(scope['scenario'],500):raise SafetyError('A bounded scenario reference is required')
    if type(scope['max_batch']) is not int or not 1<=scope['max_batch']<=20:
        raise SafetyError('Native standing batch limit must be 1..20')
    if scope['max_24h'] is not None and (type(scope['max_24h']) is not int or scope['max_24h']<scope['max_batch']):
        raise SafetyError('Invalid optional rolling 24-hour item cap')
    expiry=scope['expires']
    if expiry is not None and (type(expiry) not in (int,float) or not math.isfinite(expiry) or expiry<=now):
        raise SafetyError('Native grant is expired or has an invalid lifetime')
    rules=scope['rules']
    if not isinstance(rules,list) or not 1<=len(rules)<=50:raise SafetyError('Use 1..50 exact native grant rules')
    seen=set()
    for rule in rules:
        if not isinstance(rule,dict) or set(rule)!={'operation','source_id','container_id','fields','destinations'}:
            raise SafetyError('Unknown native grant rule fields')
        op=rule['operation'];fields=rule['fields'];dest=rule['destinations']
        if not isinstance(op,str) or op not in FIELDS[name] or not exact(rule['source_id']) or not exact(rule['container_id']):
            raise SafetyError('Native grant operation/source/container is unsupported')
        if (not isinstance(fields,list) or not fields or any(not isinstance(f,str) for f in fields)
                or len(set(fields))!=len(fields) or set(fields)-FIELDS[name][op]):
            raise SafetyError('Native grant field scope must explicitly select supported fields')
        if 'location_alarms' in fields and 'alarms' not in fields:raise SafetyError('Location-alarm authority requires explicit complete alarm-set authority')
        if (not isinstance(dest,list) or len(dest)>20 or any(not exact(d) for d in dest)
                or len(set(dest))!=len(dest) or bool(dest)!=(op in {'move_note','move_reminder','create_subtask','rename_section'})):
            raise SafetyError('Moves require exact destinations, subtask creation exact parents and section renames exact section IDs; other rules cannot grant them')
        if op=='rename_section':
            import uuid
            try:
                normalized=[str(uuid.UUID(value)).upper() for value in dest]
                if any(str(uuid.UUID(value)).upper()!=value.upper() for value in dest) or len(set(normalized))!=len(normalized):raise ValueError()
            except (ValueError,AttributeError):raise SafetyError('Section grant targets must be distinct exact UUIDs') from None
        identity=(op,rule['source_id'],rule['container_id'])
        if identity in seen:raise SafetyError('Duplicate native grant selector; combine its field scope explicitly')
        seen.add(identity)
    return scope


class NativeGrants:
    def __init__(self,engine):self.engine=engine;self.store=engine.store;self.name=engine.name;self.account=engine.account
    @contextmanager
    def locked(self):
        self.store.ready();fd=os.open(self.store.root/'native-grants.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise SafetyError('Invalid native grant lock')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);yield
        except BlockingIOError:raise SafetyError('Native grant admission is busy; inspect status') from None
        finally:os.close(fd)
    def container(self,source,container):
        status=selected_read_access(self.name)
        if source not in status['source_ids']:raise SafetyError('Native grant source is unselected')
        if container==source:return {'source_id':source,'container_id':source,'role':'account_root'}
        if self.name=='notes':return self.engine.adapter.private_folder(container,source)
        values=[c for c in self.engine.adapter.lists() if c.get('source_id')==source and c.get('id')==container]
        if len(values)!=1 or not values[0].get('writable'):raise SafetyError('Native grant list is absent or not writable')
        return values[0]
    def scope_snapshot(self,scope):
        result=[]
        for rule in scope['rules']:
            root=rule['operation'] in {'create_folder','create_list'}
            if not root and rule['container_id']==rule['source_id']:raise SafetyError('An item operation needs a real container ID')
            if rule['operation']=='create_list' and rule['container_id']!=rule['source_id']:raise SafetyError('List creation targets its selected source root')
            if self.name=='notes' and rule['operation']=='move_note':
                destinations=[];roles={};source=None
                for destination in rule['destinations']:
                    origin,target,checked=self.engine.adapter.move_folders({'source_id':rule['source_id'],
                        'folder_id':rule['container_id'],'fields':{'destination_folder_id':destination}})
                    if source is not None and source!=origin:raise SafetyError('Move source changed during grant review')
                    source=origin;destinations.append(target)
                    for role in checked:
                        # Membership writes can increment the native revision.
                        # Roles, names, account relations and ancestry stay bound.
                        stable={key:value for key,value in role.items() if key!='revision'}
                        if role['folder_id'] in roles and roles[role['folder_id']]!=stable:raise SafetyError('Move folder role changed during grant review')
                        roles[role['folder_id']]=stable
                result.append({'source':dict(source,verified_move_roles=[roles[key] for key in sorted(roles)]),'destinations':destinations})
                continue
            source=self.container(rule['source_id'],rule['container_id'])
            if (self.name=='notes' and rule['operation'] in {'copy_observation','create_note','append_note','prepend_note','replace_text','update_note','create_folder'}
                    and rule['container_id']!=rule['source_id']):
                source=self.engine.adapter.checked_content_folder_role(source,rule['source_id'])
            if rule['operation'] in {'rename_folder','rename_list'}:source={k:v for k,v in source.items() if k!='title'}
            if rule['operation']=='set_list_color':source={k:v for k,v in source.items() if k!='color'}
            if rule['operation']=='rename_folder':
                role=self.engine.adapter.reader.read('folder_role',folder_id=rule['container_id'])
                inventory=self.engine.adapter.reader.read('folder_inventory',folder_id=rule['container_id'])
                root=[f for f in inventory.get('folders',[]) if f.get('id')==rule['container_id']]
                if (role.get('storage_kind')!='regular' or role.get('source_id')!=rule['source_id'] or role.get('folder_id')!=rule['container_id']
                        or len(root)!=1 or root[0].get('is_default') is not False):raise SafetyError('Rename grant requires a verified ordinary nondefault folder')
                source['storage_role']={k:role[k] for k in ('source_id','folder_id','storage_kind','marked_for_deletion','has_smart_query','schema_sha256')}
            if rule['operation']=='create_subtask':
                destinations=[]
                for parent_id in rule['destinations']:
                    parent=self.engine.adapter.reader.read('get',id=parent_id)['item']
                    if parent.get('id')!=parent_id or parent.get('source_id')!=rule['source_id'] or parent.get('list_id')!=rule['container_id']:raise SafetyError('Subtask grant parent is absent or outside its exact source and list')
                    destinations.append({'id':parent_id,'source_id':rule['source_id'],'list_id':rule['container_id']})
            elif rule['operation']=='rename_section':
                sections=self.engine.adapter.reader.read('list_sections',list_id=rule['container_id'])
                if sections.get('source_id')!=rule['source_id'] or sections.get('list_id')!=rule['container_id']:raise SafetyError('Section grant list scope is invalid')
                destinations=[]
                for section_id in rule['destinations']:
                    matches=[s for s in sections.get('sections',[]) if isinstance(s.get('id'),str) and s['id'].upper()==section_id.upper()]
                    if len(matches)!=1:raise SafetyError('Section grant target is absent or outside the exact list')
                    destinations.append({'id':matches[0]['id'],'source_id':rule['source_id'],'list_id':rule['container_id']})
            else:destinations=[self.container(rule['source_id'],d) for d in rule['destinations']]
            result.append({'source':source,'destinations':destinations})
        return result
    def prepare(self,scope,*,_replaces=None):
        validate_scope(self.name,scope,self.store.clock())
        if self.name=='reminders' and any(rule['operation']=='move_reminder' for rule in scope['rules']):
            raise SafetyError('Reminder move grants are unavailable until complete hierarchy effects can be verified')
        context=self.engine.context(True)
        payload={'account':self.account,'context':context,'scope':scope,
             'containers':self.scope_snapshot(scope),'effects':{
                 'authority':'Ongoing exact rule scope; no per-batch receipt within the active grant. Each batch is immutable and consumed once.',
                 'lifetime':'Until revoked' if scope['expires'] is None else 'Until the selected expiry or revocation',
                 'configuration':'No service, source, OS permission or write-mode changes.',
                 'exclusions':'No permanent deletion, collaboration changes, invitations, locks, recording or external export.',
                 'sharing':'Notes private ancestors checked; EventKit does not expose reminder-list participant state.',
                 'quotas':'All admitted batch items count, including uncertain or refused items; no automatic refund.'}}
        if self.name=='notes':payload['effects']['folder_role']=('The optional native model checks selected content folders and their ancestors before admission; separate provider reads are not atomic.' if native_config('notes').get('folder_write_backend')=='notes-shared' else 'Notes content authority is limited to the account default creation folder; its identity is rechecked before dispatch.')
        if self.name=='notes' and any(rule['operation']=='move_note' for rule in scope['rules']):
            payload['effects']['move_scope']='Moves require explicitly selected read-only metadata, ordinary unshared source/destination ancestors and exact destination IDs. Roles and ancestry are rechecked at admission; every note retains its own content and attachment snapshot. No Recently Deleted, cross-account or shared moves.'
        if any(rule['operation']=='copy_observation' for rule in scope['rules']):
            payload['effects']['observation_copy']='Allows new lossy copies from caller-supplied observations in this exact scope; originals are unchanged, and copies do not retain original identity, native metadata or attachments. Duplicates may result. Each batch discloses its omissions and selected fields.'
            if self.name=='reminders':payload['effects']['observation_copy']+=' Reminder copies start incomplete; copied dates and absolute alarms can affect notifications controlled by Apple. Past alarms may notify immediately.'
        if _replaces is not None:
            payload['replaces']={'grant_id':_replaces['plan_id'],'sha256':digest(_replaces)}
            payload['previous_scope']=_replaces['payload']['scope']
            payload['effects']['replacement']='Retire the exact prior grant before activating this scope; rolling quota history carries forward.'
        return self.store.prepare(self.name+'_grant',payload)
    def prepare_replacement(self,grant_id,scope):
        return self.prepare(scope,_replaces=self.checked(grant_id))
    def lineage(self,grant):
        chain={};current=grant
        for _ in range(64):
            identity=current['plan_id']
            if identity in chain:raise SafetyError('Cyclic native grant replacement history')
            chain[identity]=current;parent=current['payload'].get('replaces')
            if parent is None:return chain
            if not isinstance(parent,dict) or set(parent)!={'grant_id','sha256'}:raise SafetyError('Invalid native replacement binding')
            previous=self.owned(parent['grant_id'])
            if parent['sha256']!=digest(previous):raise SafetyError('Native replacement history changed')
            current=previous
        raise SafetyError('Native grant replacement history exceeds the verified bound')
    def retirement(self,plan):
        parent=plan['payload'].get('replaces')
        if parent is None:return None
        previous=self.owned(parent['grant_id'])
        if (parent!={'grant_id':previous['plan_id'],'sha256':digest(previous)}
                or plan['payload'].get('previous_scope')!=previous['payload']['scope']):raise SafetyError('Native grant replacement binding or before-scope changed')
        record={'grant_sha256':digest(previous),'replaced_by':plan['plan_id'],'replacement_sha256':digest(plan)}
        return self.store.path(previous['plan_id'],'.native-grant-revoked'),record
    def retire_previous(self,plan):
        retirement=self.retirement(plan)
        if retirement is None:return
        path,record=retirement
        if path.exists():
            if self.store.read(path)!=record:raise SafetyError('Prior native grant was revoked outside this replacement; review a fresh grant')
        else:self.store.write_once(path,record)

    def owned(self,grant_id):
        plan=self.store.read(self.store.path(grant_id,'.json'))
        if plan.get('kind')!=self.name+'_grant' or plan.get('payload',{}).get('account')!=self.account:raise SafetyError('Native grant belongs to another service or owner')
        return plan
    def checked(self,grant_id,*,active=True,containers=True):
        plan=self.owned(grant_id);payload=plan['payload'];validate_scope(self.name,payload['scope'],self.store.clock())
        if self.store.path(grant_id,'.native-grant-revoked').exists():raise SafetyError('Native grant revoked')
        if payload['context']!=self.engine.context(True):raise SafetyError('Native grant access, backend or policy changed')
        if containers and payload['containers']!=self.scope_snapshot(payload['scope']):raise SafetyError('Native grant container metadata changed; review a replacement')
        if active:
            if not self.store.path(grant_id,'.result').exists() or not self.store.path(grant_id,'.started').exists():raise SafetyError('Native grant is not active')
            result=self.store.read(self.store.path(grant_id,'.result'));started=self.store.read(self.store.path(grant_id,'.started'))
            if result!={'status':'completed','grant_sha256':digest(plan),'grant_active':True} or started.get('sha256')!=digest(plan):raise SafetyError('Native grant activation is incomplete or mismatched')
            self.admitted_activation(plan)
            retired=self.retirement(plan)
            if retired is not None and (not retired[0].exists() or self.store.read(retired[0])!=retired[1]):raise SafetyError('Prior native grant retirement is not proven')
        if self.store.path(grant_id,'.native-grant-revoked').exists() or payload['context']!=self.engine.context(True):
            raise SafetyError('Native grant authority changed during container verification')
        return plan
    def admitted_activation(self,plan):
        identity=plan['plan_id'];started=self.store.read(self.store.path(identity,'.started'))
        policy=plan['payload']['context']['approval_policy']
        authority='local-receipt' if policy=='local-receipt' else 'trusted-client-policy; not an independent human receipt'
        if started.get('sha256')!=digest(plan) or started.get('account')!=self.account or started.get('authorization')!=authority:
            raise SafetyError('Native grant activation admission proof is invalid')
        if policy=='local-receipt':
            receipt=self.store.read(self.store.path(identity,'.approved'))
            if receipt.get('plan_id')!=identity or receipt.get('sha256')!=digest(plan):raise SafetyError('Native grant receipt does not match')
    def finalize(self,grant_id):
        """Complete an interrupted admitted activation; no native write or new approval."""
        selected_read_access(self.name)
        with self.engine.serialized(),self.locked():
            plan=self.checked(grant_id,active=False);self.admitted_activation(plan)
            self.retire_previous(plan)
            result={'status':'completed','grant_sha256':digest(plan),'grant_active':True}
            path=self.store.path(grant_id,'.result')
            if path.exists():
                if self.store.read(path)!=result:raise SafetyError('Native grant result is inconsistent')
                return result
            return self.store.finish(grant_id,result)
    def activate(self,grant_id):
        # Serialize ordinary activation with native mutations; admission still uses
        # the existing strict receipt/trusted-client policy, never a minted receipt.
        selected_read_access(self.name)
        with self.engine.serialized(),self.locked():
            self.engine.assert_settled();plan=self.checked(grant_id,active=False)
            parent=plan['payload'].get('replaces')
            if parent is not None:
                previous=self.checked(parent['grant_id'])
                if parent!={'grant_id':previous['plan_id'],'sha256':digest(previous)}:raise SafetyError('Prior native grant changed before replacement approval')
                self.lineage(plan)
            admitted=self.store.consume(grant_id,self.account)
            if admitted!=plan:raise SafetyError('Native grant changed at activation')
            self.retire_previous(plan)
            return self.store.finish(grant_id,{'status':'completed','grant_sha256':digest(plan),'grant_active':True})
    def revoke(self,grant_id):
        from .native_access import access_status
        access_status(self.name)
        # This short lock can run between a batch's items; it never waits for an
        # in-flight native operation to finish or claims to recall its effect.
        with self.locked():
            plan=self.owned(grant_id);path=self.store.path(grant_id,'.native-grant-revoked')
            if not path.exists():self.store.write_once(path,{'grant_sha256':digest(plan),'revoked_at':self.store.clock()})
        return {'status':'revoked','grant_id':grant_id,'in_flight_effects_recalled':False}
    def status(self,grant_id):
        from .native_access import access_status
        access_status(self.name);plan=self.owned(grant_id)
        if self.store.path(grant_id,'.native-grant-revoked').exists():state='revoked'
        elif not self.store.path(grant_id,'.started').exists():state='pending_activation'
        else:
            try:self.checked(grant_id,containers=False);state='active'
            except (SafetyError,OSError):state='inactive_or_unverified'
        try:self.engine.assert_settled();admission='requires_current_scope_and_native_checks'
        except (SafetyError,OSError):admission='blocked_by_unfinished_native_operation'
        return {'grant_id':grant_id,'status':state,'write_admission':admission,'sha256':digest(plan),'scope':plan['payload']['scope'],
                'container_state':'not_refreshed','configuration_changed':False,'replaces':plan['payload'].get('replaces')}
    def match(self,grant,item):
        scope=grant['payload']['scope'];op=item['operation']
        if op=='copy_observation':raise SafetyError('Observation copies must use the reviewed copy preparation path')
        marker=item.get('standing_operation');scope_op=op if marker is None else marker
        if marker is not None and (marker!='copy_observation' or op!=('create_note' if self.name=='notes' else 'create_reminder')):
            raise SafetyError('Invalid observation copy grant operation')
        container=(item.get('parent_folder_id',item['source_id']) if op=='create_folder' else item['source_id'] if op=='create_list'
                   else item.get('folder_id') if self.name=='notes' else item.get('list_id') if op in {'create_reminder','create_section','rename_section','rename_list','set_list_color'}
                   else item.get('before',{}).get('item',{}).get('list_id'))
        fields=item.get('requested_fields')
        if not isinstance(fields,list) or not fields:raise SafetyError('Native standing plan lacks exact requested field scope')
        requires_location=False
        if self.name=='reminders' and op in {'create_reminder','update_reminder'} and 'alarms' in fields:
            requested=item.get('fields',{}).get('alarms',[])
            prior=item.get('before',{}).get('item',{}).get('alarms',[])
            if not isinstance(requested,list) or not isinstance(prior,list):raise SafetyError('Location-alarm scope cannot be verified')
            requires_location=any(isinstance(x,dict) and 'location' in x for x in requested) or any(isinstance(x,dict) and x.get('structured_location') is not None for x in prior)
        for rule in scope['rules']:
            if (scope_op==rule['operation'] and item['source_id']==rule['source_id'] and container==rule['container_id']
                    and set(fields)<=set(rule['fields'])):
                if requires_location and 'location_alarms' not in rule['fields']:continue
                dest=item['fields'].get('destination_folder_id' if self.name=='notes' else 'list_id')
                if op in {'move_note','move_reminder'} and dest not in rule['destinations']:continue
                if op=='create_subtask' and item['id'] not in rule['destinations']:continue
                if op=='rename_section' and item['section_id'].upper() not in {value.upper() for value in rule['destinations']}:continue
                return
        raise SafetyError('Native change exceeds the approved scenario source/container/operation/fields')
    def check_batch(self,plan,grant):
        binding=plan['payload'].get('standing_grant')
        if binding!={'grant_id':grant['plan_id'],'sha256':digest(grant)}:raise SafetyError('Native standing batch does not bind the active grant')
        items=plan['payload']['changes']
        if not 1<=len(items)<=grant['payload']['scope']['max_batch']:raise SafetyError('Native standing batch exceeds its approved bound')
        copy=plan['payload'].get('copy_review');marked=[item.get('standing_operation')=='copy_observation' for item in items]
        expected={'notes':'create_lossy_note_copy_from_user_supplied_observation','reminders':'create_lossy_copies_from_user_supplied_observation'}[self.name]
        if any(marked) or copy is not None:
            if not all(marked) or not isinstance(copy,dict) or copy.get('operation')!=expected or copy.get('accepted_omissions') is not True:
                raise SafetyError('Observation copy batch lacks its exact reviewed omissions')
        for item in items:self.match(grant,item)
    def consume(self,plan_id):
        # The engine holds the native-execution lock. This lock serializes grant
        # quotas/revocation with admission. The existing ledger blocks uncertainty.
        with self.locked():
            plan=self.store.proposal(plan_id);binding=plan['payload'].get('standing_grant',{})
            grant=self.checked(binding.get('grant_id'));self.check_batch(plan,grant);lineage=self.lineage(grant)
            if plan.get('kind')!=self.name+'_change' or plan['payload'].get('account')!=self.account:raise SafetyError('Wrong native standing owner/service')
            self.engine.assert_settled(plan);now=self.store.clock();scope=grant['payload']['scope'];count=len(plan['payload']['changes']);used=0
            for path in self.store.root.glob('*.native-grant-use'):
                entry=self.store.read(path)
                if entry.get('grant_id') not in lineage:continue
                original_grant=lineage[entry['grant_id']]
                if type(entry.get('time')) not in (int,float) or not math.isfinite(entry['time']) or entry['time']>now:raise SafetyError('Native grant audit time is invalid; inspect clock changes')
                if type(entry.get('count')) is not int or entry['count']<1:raise SafetyError('Invalid native grant quota audit')
                admitted=self.store.read(self.store.path(path.name.removesuffix('.native-grant-use'),'.json'))
                if (entry.get('grant_sha256')!=digest(original_grant) or entry.get('plan_sha256')!=digest(admitted)
                        or admitted.get('payload',{}).get('standing_grant')!={'grant_id':original_grant['plan_id'],'sha256':digest(original_grant)}
                        or entry['count']!=len(admitted.get('payload',{}).get('changes',[]))):raise SafetyError('Native grant quota audit does not bind its original batch')
                if entry['time']>=now-86400:used+=entry['count']
            if scope['max_24h'] is not None and used+count>scope['max_24h']:raise SafetyError('Native grant rolling 24-hour item cap reached')
            self.store.write_once(self.store.path(plan_id,'.native-grant-use'),{'grant_id':grant['plan_id'],'grant_sha256':digest(grant),'plan_sha256':digest(plan),'count':count,'time':now})
            self.store.write_once(self.store.path(plan_id,'.started'),{'started':now,'sha256':digest(plan),'account':self.account,
                   'authorization':'standing-native-grant','grant_id':grant['plan_id'],'grant_sha256':digest(grant)})
            return plan
