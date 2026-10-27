"""Owner-authorized setup edits, separate from OS grants and native write approval.

Review digests detect stale input; they are not proof of human approval. This
local utility requires owner setup authority, just like editing private settings.
"""
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from .core import SafetyError,digest
from .local_config import read_settings
from .native_app import bundle_fingerprint
from .native_read import validate_config
from .services import configured


def private_directory(path):
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
        raise SafetyError('Setup directory must be a real, owned directory with mode 0700')


def settings_bytes(root):
    private_directory(root/'local')
    path=root/'local/settings.json'
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise SafetyError('Settings must be an owned private regular file')
        data=stream.read(100001)
    if len(data)>100000:raise SafetyError('Settings exceed the bounded setup size')
    return data


def verify_signature(helper):
    bundle=Path(helper).parent.parent.parent
    result=subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(bundle)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
    if result.returncode:raise SafetyError('Native helper signature verification failed; rebuild through the documented workflow')


def preview(root,service,helper=None,*,source_ids=None,enabled=True):
    root=Path(root).absolute()
    if service not in {'notes','reminders'} or type(enabled) is not bool:raise SafetyError('Choose Notes or Reminders explicitly')
    raw=settings_bytes(root);settings=read_settings(root)
    if settings_bytes(root)!=raw:raise SafetyError('Settings changed during setup inspection')
    fingerprint=None
    if enabled:
        if helper is None:raise SafetyError('Choose the exact signed helper for enabled setup')
        helper=Path(helper).absolute();fingerprint=bundle_fingerprint(helper);verify_signature(helper)
    elif helper is not None or source_ids is not None:raise SafetyError('Disabling preserves helper and source configuration; omit those options')
    selection={'notes':False,'reminders':False,**configured(settings)}
    previous=copy.deepcopy(settings.get('native_services',{}).get(service,{}));target=copy.deepcopy(previous)
    if enabled:
        target['helper_path']=str(helper);target.setdefault('source_ids',[])
    elif not selection.get(service,False):raise SafetyError('Native service is already disabled')
    if source_ids is not None:
        if not isinstance(source_ids,list) or any(v=='*' for v in source_ids):raise SafetyError('Select exact discovered source IDs, never a wildcard')
        target['source_ids']=list(source_ids)
    if enabled and (not selection.get(service,False) or 'access_mode' not in target):target['access_mode']='read-only'
    native=copy.deepcopy(settings.get('native_services',{}));native[service]=target;validate_config(native)
    selection[service]=enabled
    updated=copy.deepcopy(settings);updated.update(services=selection,native_services=native)
    if updated==settings:raise SafetyError('Native configuration already matches; no setup edit required')
    global_mode=settings['access_mode'];mode=target.get('access_mode','read-only')
    effective='disabled' if not enabled or 'disabled' in (global_mode,mode) else 'write-enabled' if global_mode==mode=='write-enabled' else 'read-only'
    review={'version':1,'operation':'configure_native_service','installation_root':str(root.resolve()),'service':service,'settings_sha256':hashlib.sha256(raw).hexdigest(),
        'helper_fingerprint':fingerprint,'before':{'enabled':configured(settings).get(service,False),'configuration':previous},
        'after':{'enabled':enabled,'configuration':target,'effective_access_after_restart':effective},
        'effects':{'source_discovery_only':not target.get('source_ids',[]),'source_ids_verified':False,
                   'os_permissions_changed':False,'native_data_changed':False,'approval_policy_changed':False,
                   'server_restart_required':True,'running_servers':'Existing processes retain their current environment until restarted.',
                   'authorization':'Owner setup authority is required. This digest is a concurrency check, not a native write receipt.'}}
    return {'review':review,'sha256':digest(review)},updated,raw


@contextmanager
def setup_lock(root):
    path=root/'local/native-setup.lock';fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise SafetyError('Unsafe native setup lock')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SafetyError('Another native setup edit is active') from None
        yield
    finally:os.close(fd)


def apply(root,service,helper,review_sha256,*,source_ids=None,enabled=True):
    root=Path(root).absolute();private_directory(root/'local')
    with setup_lock(root):
        proposal,updated,raw=preview(root,service,helper,source_ids=source_ids,enabled=enabled)
        if proposal['sha256']!=review_sha256:raise SafetyError('Setup review is stale; inspect a new preview before applying')
        from .approvals import PlanStore
        from .native_changes import NativeChanges
        settings=read_settings(root);runtime=Path(settings['runtime_directory']) if settings.get('runtime_directory') else Path.home()/'Library/Application Support/icloud-for-agents/runtime'
        engine=NativeChanges(service,settings['account'],None,PlanStore(runtime))
        with engine.serialized():
            engine.assert_settled()
            if settings_bytes(root)!=raw:raise SafetyError('Settings changed before the setup edit')
            if enabled and bundle_fingerprint(helper)!=proposal['review']['helper_fingerprint']:raise SafetyError('Helper changed before the setup edit')
            backup=root/'local'/('settings.before-native-'+proposal['review']['settings_sha256']+'.json')
            try:
                fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            except FileExistsError:
                info=backup.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or backup.read_bytes()!=raw:raise SafetyError('Existing setup backup is unsafe or differs')
            else:
                with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(mode='w',dir=root/'local',prefix='.native-settings-',delete=False) as stream:
                    temporary=Path(stream.name);os.fchmod(stream.fileno(),0o600);json.dump(updated,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
                if settings_bytes(root)!=raw:raise SafetyError('Settings changed during the setup edit')
                os.replace(temporary,root/'local/settings.json');temporary=None
                fd=os.open(root/'local',os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
            finally:
                if temporary is not None:temporary.unlink(missing_ok=True)
    return {'status':'configured','service':service,'review_sha256':review_sha256,'backup_path':str(backup),
            'server_restart_required':True,'effective_access_after_restart':proposal['review']['after']['effective_access_after_restart'],
            'os_permissions_changed':False,'native_data_changed':False}
