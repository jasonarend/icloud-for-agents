"""One-shot native application launch with owner-private file exchange.

No listener, daemon, arbitrary command selection or independently authenticated
remote approval is provided. This is a same-owner local helper transport.
"""
import json
import hashlib
import plistlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import shutil

from .approvals import PlanStore, runtime_root
from .core import SafetyError


def _owned_asset(path,*,directory=False,limit=None):
    try:info=path.lstat()
    except OSError:raise SafetyError('Native app assets are unavailable') from None
    valid_type=stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid!=os.getuid() or info.st_mode & 0o022 or (limit is not None and info.st_size>limit):
        raise SafetyError('Native app assets must be owned, regular and not writable by other users')


def validate_bundle(helper):
    """Validate the entrypoint and bundled scripts, not just one executable file."""
    helper=Path(helper);bundle=helper.parent.parent.parent
    if bundle.suffix!='.app':raise SafetyError('Notes requires the built native helper app bundle')
    for directory in (bundle,bundle/'Contents',helper.parent,bundle/'Contents/Resources'):
        _owned_asset(directory,directory=True)
    _owned_asset(helper,limit=50_000_000)
    if not os.access(helper,os.X_OK):raise SafetyError('Native helper must be executable')
    plist=bundle/'Contents/Info.plist';_owned_asset(plist,limit=65536)
    for name in ('notes.js','notes-write.js'):_owned_asset(bundle/'Contents/Resources'/name,limit=1_000_000)
    try:
        info=plistlib.loads(plist.read_bytes())
        if (not isinstance(info,dict) or info.get('CFBundleExecutable')!=helper.name
                or info.get('CFBundleIdentifier')!='com.jasonarend.icloud-for-agents.native'
                or info.get('CFBundlePackageType')!='APPL'):raise ValueError()
    except (OSError,ValueError,TypeError,plistlib.InvalidFileException):
        raise SafetyError('Native app entrypoint or identity does not match the configured helper') from None
    return bundle


def exchange_base():
    return Path.home()/'Library/Application Support/icloud-for-agents/native-exchange'


def exchange_root():
    # One-shot request files belong in the local app-support location, even when
    # the audit workspace is on an external or cloud-backed volume. Each ledger
    # keeps a distinct namespace; relocating IPC never relocates/clears approvals.
    identity=hashlib.sha256(str(runtime_root().resolve()).encode()).hexdigest()
    return exchange_base()/identity/'native-app'


def application_request(helper, request, timeout=25):
    helper=Path(helper)
    bundle=validate_bundle(helper)
    runtime=runtime_root()
    PlanStore(runtime).ready()
    PlanStore(exchange_base()).ready()
    PlanStore(exchange_root().parent).ready()
    root=exchange_root()
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
        raise SafetyError('Native application exchange must be owner-private')
    directory=Path(tempfile.mkdtemp(prefix='request-',dir=root)).resolve()
    request=dict(request,request_id=directory.name)
    encoded=json.dumps(request,ensure_ascii=False).encode()
    if len(encoded)>65536:
        shutil.rmtree(directory)
        raise SafetyError('Native request exceeds input limit')
    request_path=directory/'request.json'
    fd=os.open(request_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(encoded);stream.flush();os.fsync(stream.fileno())
    env={k:os.environ[k] for k in ('HOME','PATH','TMPDIR','LANG') if k in os.environ}
    try:
        if request.get('service')=='notes' and request.get('action') in {'sources','folders','folder_get','query','get','search','attachments','diagnose_note','contains','apply','request_access','diagnose_read_access'}:
            # Normal application launch, not Automation consent or a fallback
            # host. Status stays nonlaunching. Wake the fixed target before the
            # helper's already-running-target permission preflight.
            target=subprocess.run(['/usr/bin/open','-g','-a','/System/Applications/Notes.app'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,env=env)
            if target.returncode:raise SafetyError('Notes application launch failed before helper dispatch; inspect pending request status')
        process=subprocess.run(['/usr/bin/open','-W','-n',str(bundle), '--args','--ipc-directory',str(directory)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout,env=env)
        if process.returncode:raise SafetyError('Native application launch failed; inspect pending request status')
        fd=os.open(directory/'result.json',os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:
                raise SafetyError('Native application result is not owner-private')
            raw=stream.read(2_000_001)
        if len(raw)>2_000_000:raise SafetyError('Native result exceeds limit')
        result=json.loads(raw)
        if not isinstance(result,dict) or result.get('protocol_version')!=1 or result.get('request_id')!=directory.name:raise ValueError()
        if "error" not in result:shutil.rmtree(directory)
        return result
    except SafetyError:raise
    except (OSError,ValueError,subprocess.SubprocessError):
        # Keep private input and late response for reconciliation. Never replay here.
        raise SafetyError('Native application failed or timed out; inspect pending request status before retry') from None


def pending_requests(service):
    """Include retained requests from both transports; never hide older uncertainty."""
    current=_pending_at(exchange_root(),service)
    legacy=_pending_at(runtime_root()/'native-app',service)
    return current+legacy


def _pending_at(root,service):
    """Metadata only: never expose an old note body or an unselected source's data."""
    if not root.exists():return []
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
        raise SafetyError('Invalid native application exchange')
    result=[]
    for directory in sorted(root.glob('request-*'))[:100]:
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise SafetyError('Invalid pending native request')
        fd=os.open(directory/'request.json',os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:
                raise SafetyError('Invalid pending native request file')
            raw=stream.read(65537)
        if len(raw)>65536:raise SafetyError('Oversized pending native request')
        request=json.loads(raw)
        if request.get('service')!=service:continue
        result.append({'request_id':directory.name,'action':request.get('action'),
                       'state':'response_available' if (directory/'result.json').exists() else 'pending_or_unfinished',
                       'automatic_retry':False})
    return result
