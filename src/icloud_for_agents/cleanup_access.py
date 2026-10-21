"""Disabled-by-default, owner-installed standing MAIL cleanup authorization.

No MCP tool creates/activates a grant. Local owned files are a workflow boundary,
not protection against another process already running as the same OS user.
"""
import math
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
import time
import uuid
from .approvals import PlanStore, runtime_root
from .core import SafetyError, batch, canonical, uid

ALLOWED = {'archive', 'mark_read', 'trash'}


def validate(scope, account, now):
    keys={'grant_id','account','operations','source_folders','destinations','max_batch','max_24h','issued','expires','rules_reference'}
    if not isinstance(scope,dict) or set(scope)!=keys or scope['account']!=account:
        raise SafetyError('Standing cleanup account/schema mismatch')
    ops=scope['operations'];folders=scope['source_folders'];dest=scope['destinations']
    if not isinstance(ops,list) or not ops or len(set(ops))!=len(ops) or set(ops)-ALLOWED:
        raise SafetyError('Standing cleanup never covers send, DAV or purge')
    if not isinstance(folders,list) or not 1<=len(folders)<=10 or len(set(folders))!=len(folders):
        raise SafetyError('Standing cleanup requires explicit source folders')
    if not isinstance(dest,dict) or set(dest)-{'archive','trash'}:
        raise SafetyError('Standing cleanup destination schema mismatch')
    for value in folders+list(dest.values()):
        if not isinstance(value,str) or not value or len(value)>1000 or any(ord(c)<32 for c in value) or value=='*':
            raise SafetyError('Standing cleanup needs exact folder names, no wildcards')
    if any(op in ops and op not in dest for op in ('archive','trash')) or len(set(dest.values()))!=len(dest) or set(dest.values())&set(folders):
        raise SafetyError('Standing cleanup destinations must be explicit and distinct')
    if type(scope['max_batch']) is not int or not 1<=scope['max_batch']<=100 or (scope['max_24h'] is not None and (type(scope['max_24h']) is not int or scope['max_24h']<scope['max_batch'])):
        raise SafetyError('Invalid bounded standing cleanup quota')
    if type(scope['issued']) not in (int,float) or not math.isfinite(scope['issued']) or scope['issued']>now+60 or (scope['expires'] is not None and (type(scope['expires']) not in (int,float) or not math.isfinite(scope['expires']) or scope['expires']<=scope['issued'] or now>=scope['expires'])):
        raise SafetyError('Standing cleanup expired or has invalid lifetime')
    if not isinstance(scope['rules_reference'],str) or not 1<=len(scope['rules_reference'])<=500:
        raise SafetyError('Standing cleanup needs a task-rules reference')
    if not isinstance(scope['grant_id'],str) or len(scope['grant_id'])!=32 or any(c not in '0123456789abcdef' for c in scope['grant_id']):
        raise SafetyError('Invalid standing cleanup identity')
    return scope


class CleanupAccess:
    def __init__(self, grant_id, account, root=None, clock=time.time):
        self.grant_id,self.account,self.clock=grant_id,account,clock
        base=Path(root) if root is not None else runtime_root()
        self.authority=PlanStore(base/'cleanup-authority',clock=clock)
        self.audit=PlanStore(base/'cleanup-audit',clock=clock)

    def scope(self):
        if self.authority.path(self.grant_id,'.revoked').exists():
            raise SafetyError('Standing cleanup revoked')
        value=self.authority.read(self.authority.path(self.grant_id,'.json'))
        if value.get('grant_id')!=self.grant_id:raise SafetyError('Standing cleanup identity mismatch')
        return validate(value,self.account,self.clock())

    def check(self, operation, folder, uids, destination=None):
        scope=self.scope();uids=batch(uids)
        if operation not in scope['operations'] or folder not in scope['source_folders'] or len(uids)>scope['max_batch']:
            raise SafetyError('Operation outside exact standing cleanup scope')
        if operation in {'archive','trash'} and destination!=scope['destinations'][operation]:
            raise SafetyError('Destination outside exact standing cleanup scope')
        return scope

    def run(self, operation, folder, validity, uids, destination, snapshot, action):
        validity=uid(validity)
        scope=self.check(operation,folder,uids,destination)
        if sorted(item['uid'] for item in snapshot)!=sorted(uids) or any('\\Flagged' in item['flags'] or '\\Deleted' in item['flags'] for item in snapshot):
            raise SafetyError('Standing cleanup refuses stars, deleted or mismatched messages')
        self.audit.ready();lock=self.audit.root/'ledger.lock'
        fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_uid!=os.getuid() or os.fstat(fd).st_mode&0o777!=0o600:
                raise SafetyError('Standing cleanup ledger permissions invalid')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            scope=self.check(operation,folder,uids,destination)  # revocation/expiry recheck under lock
            now=self.clock();recent=set()
            for path in self.audit.root.glob('*.started'):
                entry=self.audit.read(path)
                if entry.get('account')!=self.account:continue
                result_path=path.with_suffix('.result')
                if not result_path.exists() or self.audit.read(result_path).get('status')!='completed':
                    raise SafetyError('Uncertain standing cleanup exists; reconcile before any new write')
                if entry['time']>=now-86400:
                    recent.update((entry['folder'],entry['uidvalidity'],n) for n in entry['uids'])
            requested={(folder,validity,n) for n in uids}
            if scope['max_24h'] is not None and len(recent|requested)>scope['max_24h']:
                raise SafetyError('Standing cleanup rolling24h message limit reached')
            identity={'account':self.account,'grant_id':self.grant_id,'operation':operation,'folder':folder,'uidvalidity':validity,'uids':sorted(uids),'destination':destination}
            action_id=hashlib.sha256(canonical(identity).encode()).hexdigest()[:32]
            started=self.audit.path(action_id,'.started')
            if started.exists():raise SafetyError('Standing cleanup action already attempted; do not replay')
            self.audit.write_once(started,dict(identity,time=now,scope_sha256=hashlib.sha256(canonical(scope).encode()).hexdigest(),rules_reference=scope['rules_reference']))
            try:
                result=action()
                if result.get('status') in {'failed_or_uncertain','started_or_uncertain'}:raise SafetyError('Uncertain action result')
            except Exception:
                self.audit.finish(action_id,{'status':'uncertain','do_not_retry':True})
                raise SafetyError('Standing cleanup result uncertain; inspect exact audit/source/destination, no retry') from None
            self.audit.finish(action_id,{'status':'completed','result':result,'time':self.clock()})
            return dict(result,cleanup_authorization={'grant_id':self.grant_id,'action_id':action_id})
        finally:os.close(fd)


def configured(account):
    value=os.environ.get('ICLOUD_CLEANUP_GRANT_ID')
    if os.environ.get("ICLOUD_ENABLE_POLICY_MANAGEMENT") == "1":
        from .managed_policy import ManagedCleanupAccess
        if not value:
            raise SafetyError("Managed policy requires an owner-approved base grant")
        return ManagedCleanupAccess(value,account)
    return CleanupAccess(value,account) if value else None


def main():
    import argparse
    from .local_config import read_settings
    parser=argparse.ArgumentParser(description='OWNER ONLY: review/revoke standing mail cleanup authorization; never run by the assistant')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--review-activate',type=Path);group.add_argument('--revoke');group.add_argument('--status')
    parser.add_argument('--project-root',type=Path,default=Path.cwd())
    args=parser.parse_args();root=args.project_root.resolve();settings=read_settings(root)
    if settings.get('runtime_directory'):os.environ['ICLOUD_RUNTIME_ROOT']=settings['runtime_directory']
    if args.review_activate:
        request=json.loads(args.review_activate.read_text())
        if set(request)!={'account','operations','source_folders','destinations','max_batch','max_24h','days','rules_reference'}:raise SafetyError('Invalid grant request')
        if request['days'] is not None and (type(request['days']) is not int or request['days']<1):raise SafetyError('Grant duration must be positive days or null for no expiry')
        now=time.time();scope={k:v for k,v in request.items() if k!='days'}
        scope.update(grant_id=uuid.uuid4().hex,issued=now,expires=None if request['days'] is None else now+request['days']*86400)
        validate(scope,settings['account'],now)
        phrase='ENABLE STANDING MAIL CLEANUP '+scope['grant_id']
        with open('/dev/tty','r+',encoding='utf-8') as tty:
            tty.write(json.dumps(scope,ensure_ascii=True,indent=2)+'\nPersistent mail changes including recoverable Trash will run without daily receipts. Content categories are enforced by your task, not semantic server checks.\nType: '+phrase+'\n> ');tty.flush()
            if tty.readline().rstrip('\n')!=phrase:raise SafetyError('No authorization recorded')
        access=CleanupAccess(scope['grant_id'],settings['account']);access.authority.write_once(access.authority.path(scope['grant_id'],'.json'),scope)
        settings['cleanup_grant_id']=scope['grant_id'];target=root/'local/settings.json';temp=target.with_name('settings.grant-new.json')
        with temp.open('x') as f:os.fchmod(f.fileno(),0o600);json.dump(settings,f,indent=2);f.flush();os.fsync(f.fileno())
        temp.replace(target);print('Owner grant installed. Restart idle runtime to load exact grant; no mail changed.')
    else:
        identity=args.revoke or args.status;access=CleanupAccess(identity,settings['account'])
        if args.revoke:
            access.authority.write_once(access.authority.path(identity,'.revoked'),{'revoked':time.time()});print('Revoked for next batch; in-flight requests cannot be recalled.')
        else:print(json.dumps(access.scope(),indent=2))
