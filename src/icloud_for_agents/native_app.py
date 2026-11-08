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


def is_app_bundle_helper(helper):
    """Identify configured app entrypoints; validation must precede launching one."""
    return Path(helper).parent.parent.parent.suffix == '.app'


def _owned_asset(path,*,directory=False,limit=None):
    try:info=path.lstat()
    except OSError:raise SafetyError('Native app assets are unavailable') from None
    valid_type=stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid!=os.getuid() or info.st_mode & 0o022 or (limit is not None and info.st_size>limit):
        raise SafetyError('Native app assets must be owned, regular and not writable by other users')


def validate_bundle(helper):
    """Validate the entrypoint and bundled scripts, not just one executable file."""
    helper=Path(helper);bundle=helper.parent.parent.parent
    if bundle.suffix!='.app':raise SafetyError('This transport requires the built native helper app bundle')
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


def bundle_fingerprint(helper):
    helper=Path(helper);bundle=validate_bundle(helper);checksum=hashlib.sha256()
    for path in (helper,bundle/'Contents/Info.plist',bundle/'Contents/Resources/notes.js',bundle/'Contents/Resources/notes-write.js'):
        checksum.update(path.read_bytes())
    processor=bundle/'Contents/MacOS/icloud-agents-image-text'
    if processor.exists() or processor.is_symlink():checksum.update(image_processor(helper).read_bytes())
    return checksum.hexdigest()


def image_processor(helper):
    """Optional bundled byte-only processor; legacy bundles keep ordinary reads."""
    helper=Path(helper);bundle=validate_bundle(helper);processor=bundle/'Contents/MacOS/icloud-agents-image-text'
    if helper.parent!=processor.parent:raise SafetyError('Image OCR requires the standard helper bundle layout')
    _owned_asset(processor.parent,directory=True)
    try:_owned_asset(processor,limit=20_000_000)
    except (OSError,SafetyError):raise SafetyError('Image OCR requires an updated helper with its owned bundled processor') from None
    if not os.access(processor,os.X_OK) or processor.stat().st_nlink!=1:raise SafetyError('Image OCR processor must be executable and have one link')
    return processor


def exchange_base():
    return Path.home()/'Library/Application Support/icloud-for-agents/native-exchange'


def exchange_root():
    # One-shot request files belong in the local app-support location, even when
    # the audit workspace is on an external or cloud-backed volume. Each ledger
    # keeps a distinct namespace; relocating IPC never relocates/clears approvals.
    identity=hashlib.sha256(str(runtime_root().resolve()).encode()).hexdigest()
    return exchange_base()/identity/'native-app'


def application_request(helper, request, timeout=40):
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
    if len(encoded)>240000:
        shutil.rmtree(directory)
        raise SafetyError('Native request exceeds input limit')
    request_path=directory/'request.json'
    fd=os.open(request_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(encoded);stream.flush();os.fsync(stream.fileno())
    execution=None
    if request.get('service')=='notes' and request.get('action')=='apply':
        from .native_execution import current
        from .core import digest
        execution=current('notes')
        if execution:
            store=execution['store'];plan_id=execution['plan_id'];plan=store.read(store.path(plan_id,'.json'))
            if (digest(plan)!=execution['plan_sha256'] or store.read(store.path(plan_id,'.started')).get('sha256')!=digest(plan)
                    or plan.get('kind')!='notes_change' or type(execution['index']) is not int or not 0<=execution['index']<len(plan['payload']['changes'])):
                raise SafetyError('Native execution provenance does not match an admitted plan')
            from .native_protocol import mutation_request
            original=dict(request);original.pop('request_id',None)
            if original!=mutation_request('notes',plan['payload']['changes'][execution['index']]):raise SafetyError('Native request differs from its admitted item')
            store.write_once(store.path(plan_id,'.native-exchange-'+str(execution['index'])),
                {'plan_sha256':digest(plan),'request_id':directory.name,'request_sha256':digest(original),
                 'helper_fingerprint':bundle_fingerprint(helper),'backend_id':plan['payload']['context']['backend'],'exchange_directory':str(directory)})
    env={k:os.environ[k] for k in ('HOME','PATH','TMPDIR','LANG') if k in os.environ}
    try:
        process=subprocess.run(['/usr/bin/open','-W','-n',str(bundle), '--args','--ipc-directory',str(directory)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout,env=env)
        # Launch Services can report failure after the helper delivered a full
        # result. Fixed Reminders reads can use a validated, stable, exact-nonce
        # result without replay. Consent and writes still require launcher success.
        from .native_read import READ_ACTIONS
        read_result = (request.get('service')=='reminders' and request.get('action') in READ_ACTIONS['reminders'])
        if process.returncode and request.get('action') not in {'status','store_status'} and not read_result:
            raise SafetyError('Native application launch failed; inspect pending request status')
        fd=os.open(directory/'result.json',os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
                raise SafetyError('Native application result is not owner-private')
            raw=stream.read(2_000_001)
            after=os.fstat(stream.fileno())
            signature=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns,value.st_nlink)
            if signature(info)!=signature(after) or len(raw)!=after.st_size:
                raise SafetyError('Native application result changed during read')
        if len(raw)>2_000_000:raise SafetyError('Native result exceeds limit')
        result=json.loads(raw)
        if not isinstance(result,dict) or result.get('protocol_version')!=1 or result.get('request_id')!=directory.name:raise ValueError()
        if process.returncode:result['transport_warning']='launcher_nonzero_result_received'
        if execution:
            # Keep the exact terminal response before successful transport
            # cleanup. Independent native readback can still fail afterward.
            store.write_once(store.path(plan_id,'.native-terminal-'+str(execution['index'])),
                {'plan_sha256':digest(plan),'request_sha256':digest(original),'request_id':directory.name,'response':result})
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
            raw=stream.read(240001)
        if len(raw)>240000:raise SafetyError('Oversized pending native request')
        request=json.loads(raw)
        if request.get('service')!=service:continue
        result.append({'request_id':directory.name,'action':request.get('action'),
                       'state':'response_available' if (directory/'result.json').exists() else 'pending_or_unfinished',
                       'automatic_retry':False})
    return result


def completed_exchange_evidence(expected,*,plan_id=None,store=None):
    """Bind one completed retained exchange to an exact immutable request.

    Metadata only. A missing response cannot be treated as a stopped helper, and
    no request is replayed or removed. Ambiguous duplicate exchanges fail closed.
    """
    from .core import digest
    matches=[];scanned=0;bound=None
    roots=tuple(dict.fromkeys(root.resolve() for root in (exchange_root(),runtime_root()/'native-app')))
    if plan_id is not None:
        store=store or PlanStore();binding=store.path(plan_id,'.native-exchange-0')
        if binding.exists():
            record=store.read(binding);plan=store.read(store.path(plan_id,'.json'));directory=Path(record['exchange_directory'])
            base='notes-creation-v1:'+record.get('helper_fingerprint','');backend=plan.get('payload',{}).get('context',{}).get('backend')
            if (record.get('plan_sha256')!=digest(plan) or record.get('request_sha256')!=digest(expected)
                    or backend!=record.get('backend_id',base) or not (backend==base or isinstance(backend,str) and backend.startswith(base+':notes-store-role-v1:'))
                    or directory.parent not in roots or directory.name!=record.get('request_id')):
                raise SafetyError('Native exchange does not bind the admitted plan, helper or ledger')
            bound=directory
            if not directory.exists() and not directory.is_symlink():
                terminal=store.path(plan_id,'.native-terminal-0')
                if terminal.exists():
                    retained=store.read(terminal);response=retained.get('response',{})
                    if (retained.get('plan_sha256')!=digest(plan) or retained.get('request_sha256')!=digest(expected)
                            or retained.get('request_id')!=record['request_id'] or response.get('protocol_version')!=1
                            or response.get('request_id')!=record['request_id']):raise SafetyError('Retained native response does not bind the admitted request')
                    return {'request_id':record['request_id'],'request_sha256':digest(expected),'helper_response_sha256':digest(response),
                            'helper_outcome':'error_response' if 'error' in response else 'success_response',
                            'mutation_dispatched':response.get('mutation_dispatched') if type(response.get('mutation_dispatched')) is bool else 'unverified'}
                # Older transports discarded successful responses before
                # readback. A durable returned-ID attempt proves that the
                # synchronous transport returned; it is not a raw response.
                result=store.read(store.path(plan_id,'.result'));items=plan.get('payload',{}).get('changes',[])
                attempts=result.get('attempted',[])
                if (plan.get('kind')!='notes_change' or len(items)!=1 or items[0].get('operation')!='move_note'
                        or result.get('status')!='failed_or_uncertain' or result.get('phase')!='verification'
                        or result.get('completed')!=[] or len(attempts)!=1
                        or store.read(store.path(plan_id,'.started')).get('sha256')!=digest(plan)):
                    raise SafetyError('Missing native terminal response and no exact durable returned move attempt')
                attempt=attempts[0];outcome=attempt.get('result',{});item=items[0]
                if (attempt.get('index')!=0 or attempt.get('operation')!='move_note'
                        or outcome.get('id')!=item.get('id') or outcome.get('source_id')!=item.get('source_id')
                        or outcome.get('folder_id')!=item.get('fields',{}).get('destination_folder_id') or outcome.get('saved_locally') is not True
                        or store.read(store.path(plan_id,'.attempt-0'))!={'plan_sha256':digest(plan),'attempt':attempt}
                        or set(store.root.glob(plan_id+'.attempt-*'))!={store.path(plan_id,'.attempt-0')}):
                    raise SafetyError('Durable move attempt does not match the exact admitted identity and destination')
                return {'request_id':record['request_id'],'request_sha256':digest(expected),
                        'evidence_type':'durable_returned_move_attempt','attempt_sha256':digest(attempt),
                        'helper_outcome':'returned_result','raw_terminal_response_retained':False,'mutation_dispatched':'unverified'}
    for root in roots:
        if not root.exists():continue
        info=root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise SafetyError('Invalid native exchange evidence directory')
        for directory in ([bound] if bound is not None and bound.parent==root else [] if bound is not None else sorted(root.glob('request-*'))):
            scanned+=1
            if scanned>1000:raise SafetyError('Native exchange evidence exceeds its bounded review limit')
            info=directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
                raise SafetyError('Invalid native exchange evidence request')
            store=PlanStore(directory)
            if not (directory/'request.json').exists():continue
            request=store.read(directory/'request.json');nonce=request.get('request_id');body=dict(request);body.pop('request_id',None)
            if body!=expected:continue
            if nonce!=directory.name or not (directory/'result.json').exists():
                raise SafetyError('Matching native request has no bound terminal response; outcome remains uncertain')
            result=store.read(directory/'result.json')
            if result.get('protocol_version')!=1 or result.get('request_id')!=nonce:
                raise SafetyError('Native terminal response does not match its exact request')
            matches.append({'request_id':nonce,'request_sha256':digest(expected),'helper_response_sha256':digest(result),
                            'helper_outcome':'error_response' if 'error' in result else 'success_response',
                            'mutation_dispatched':result.get('mutation_dispatched') if type(result.get('mutation_dispatched')) is bool else 'unverified'})
    if len(matches)!=1:raise SafetyError('Known move recovery requires one unambiguous completed native exchange')
    return matches[0]
