"""One-shot native application launch with owner-private file exchange.

No listener, daemon, arbitrary command selection or independently authenticated
remote approval is provided. This is a same-owner local helper transport.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import shutil

from .approvals import PlanStore, runtime_root
from .core import SafetyError


def application_request(helper, request, timeout=25):
    helper=Path(helper)
    if helper.is_symlink() or not helper.is_file():raise SafetyError('Native helper must be a regular executable')
    info=helper.stat()
    if info.st_uid!=os.getuid() or info.st_mode & 0o022 or not os.access(helper,os.X_OK):
        raise SafetyError('Native helper must be owner-controlled and executable')
    bundle=helper.parent.parent.parent
    if bundle.suffix!='.app' or not (bundle/'Contents/Info.plist').is_file():
        raise SafetyError('Notes application transport requires a built helper app bundle')
    runtime=runtime_root()
    PlanStore(runtime).ready()
    root=runtime/'native-app'
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
        shutil.rmtree(directory)
        return result
    except SafetyError:raise
    except (OSError,ValueError,subprocess.SubprocessError):
        # Keep private input and late response for reconciliation. Never replay here.
        raise SafetyError('Native application failed or timed out; inspect pending request status before retry') from None


def pending_requests(service):
    """Metadata only: never expose an old note body or an unselected source's data."""
    root=runtime_root()/'native-app'
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
