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

FIELDS={
 'notes':{'create_note':{'title','body','format'},'append_note':{'body','format'},
          'update_note':{'title','body','format'},'move_note':{'destination_folder_id'},'create_folder':{'title'}},
 'reminders':{'create_reminder':{'title','notes','priority','due','start'},'update_reminder':{'title','notes','priority','due','start'},
              'set_completed':{'completed'},'move_reminder':{'list_id'},'create_list':{'title'}}}


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
        if (not isinstance(dest,list) or len(dest)>20 or any(not exact(d) for d in dest)
                or len(set(dest))!=len(dest) or bool(dest)!=(op in {'move_note','move_reminder'})):
            raise SafetyError('Moves require exact destinations; other rules cannot grant them')
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
            result.append({'source':self.container(rule['source_id'],rule['container_id']),
                           'destinations':[self.container(rule['source_id'],d) for d in rule['destinations']]})
        return result
    def prepare(self,scope):
        validate_scope(self.name,scope,self.store.clock());context=self.engine.context(True)
        return self.store.prepare(self.name+'_grant',{'account':self.account,'context':context,'scope':scope,
             'containers':self.scope_snapshot(scope),'effects':{
                 'authority':'Ongoing exact rule scope; no per-batch receipt within the active grant. Each batch is immutable and consumed once.',
                 'lifetime':'Until revoked' if scope['expires'] is None else 'Until the selected expiry or revocation',
                 'configuration':'No service, source, OS permission or write-mode changes.',
                 'exclusions':'No permanent deletion, collaboration changes, invitations, locks, recording or external export.',
                 'sharing':'Notes private ancestors checked; EventKit does not expose reminder-list participant state.',
                 'quotas':'All admitted batch items count, including uncertain or refused items; no automatic refund.'}})
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
            admitted=self.store.consume(grant_id,self.account)
            if admitted!=plan:raise SafetyError('Native grant changed at activation')
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
        return {'grant_id':grant_id,'status':state,'sha256':digest(plan),'scope':plan['payload']['scope'],
                'container_state':'not_refreshed','configuration_changed':False}
    def match(self,grant,item):
        scope=grant['payload']['scope'];op=item['operation']
        container=(item.get('parent_folder_id',item['source_id']) if op=='create_folder' else item['source_id'] if op=='create_list'
                   else item.get('folder_id') if self.name=='notes' else item.get('list_id') if op=='create_reminder'
                   else item.get('before',{}).get('item',{}).get('list_id'))
        fields=item.get('requested_fields')
        if not isinstance(fields,list) or not fields:raise SafetyError('Native standing plan lacks exact requested field scope')
        for rule in scope['rules']:
            if (op==rule['operation'] and item['source_id']==rule['source_id'] and container==rule['container_id']
                    and set(fields)<=set(rule['fields'])):
                dest=item['fields'].get('destination_folder_id' if self.name=='notes' else 'list_id')
                if op in {'move_note','move_reminder'} and dest not in rule['destinations']:continue
                return
        raise SafetyError('Native change exceeds the approved scenario source/container/operation/fields')
    def check_batch(self,plan,grant):
        binding=plan['payload'].get('standing_grant')
        if binding!={'grant_id':grant['plan_id'],'sha256':digest(grant)}:raise SafetyError('Native standing batch does not bind the active grant')
        items=plan['payload']['changes']
        if not 1<=len(items)<=grant['payload']['scope']['max_batch']:raise SafetyError('Native standing batch exceeds its approved bound')
        for item in items:self.match(grant,item)
    def consume(self,plan_id):
        # The engine holds the native-execution lock. This lock serializes grant
        # quotas/revocation with admission. The existing ledger blocks uncertainty.
        with self.locked():
            plan=self.store.proposal(plan_id);binding=plan['payload'].get('standing_grant',{})
            grant=self.checked(binding.get('grant_id'));self.check_batch(plan,grant)
            if plan.get('kind')!=self.name+'_change' or plan['payload'].get('account')!=self.account:raise SafetyError('Wrong native standing owner/service')
            self.engine.assert_settled(plan);now=self.store.clock();scope=grant['payload']['scope'];count=len(plan['payload']['changes']);used=0
            for path in self.store.root.glob('*.native-grant-use'):
                entry=self.store.read(path)
                if entry.get('grant_id')!=grant['plan_id']:continue
                if type(entry.get('time')) not in (int,float) or not math.isfinite(entry['time']) or entry['time']>now:raise SafetyError('Native grant audit time is invalid; inspect clock changes')
                if type(entry.get('count')) is not int or entry['count']<1:raise SafetyError('Invalid native grant quota audit')
                admitted=self.store.read(self.store.path(path.name.removesuffix('.native-grant-use'),'.json'))
                if (entry.get('grant_sha256')!=digest(grant) or entry.get('plan_sha256')!=digest(admitted)
                        or entry['count']!=len(admitted.get('payload',{}).get('changes',[]))):raise SafetyError('Native grant quota audit does not bind its original batch')
                if entry['time']>=now-86400:used+=entry['count']
            if scope['max_24h'] is not None and used+count>scope['max_24h']:raise SafetyError('Native grant rolling 24-hour item cap reached')
            self.store.write_once(self.store.path(plan_id,'.native-grant-use'),{'grant_id':grant['plan_id'],'grant_sha256':digest(grant),'plan_sha256':digest(plan),'count':count,'time':now})
            self.store.write_once(self.store.path(plan_id,'.started'),{'started':now,'sha256':digest(plan),'account':self.account,
                   'authorization':'standing-native-grant','grant_id':grant['plan_id'],'grant_sha256':digest(grant)})
            return plan
