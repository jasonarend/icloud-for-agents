"""Approved, bounded text-file insertion into one private ordinary root reminder."""
import base64
import binascii
from copy import deepcopy
import hashlib
import math
import uuid
from .core import SafetyError, canonical
from .native_once import planning_access
from .native_reminder_files import validate_id, export
from .native_reminder_moves import list_scope, check_item_graph

MAX_BYTES=16384
COVERAGE='private_ordinary_list_bounded_root_tasks_order_sections_and_exposed_attachment_inventories'
RECOVERY_EFFECT='Compare only the reviewed list, bounded tasks/order and exposed attachment inventories. An unchanged observation does not prove absence of Apple-managed temporary files, delayed effects, hidden metadata or global attachment records. No cleanup, rollback or replay.'


def data(fields):
    if not isinstance(fields,dict) or set(fields)!={'uti','data_base64','allow_mcp_only'} or fields.get('uti')!='public.plain-text' or fields.get('allow_mcp_only') is not True:
        raise SafetyError('Supply public.plain-text, canonical data_base64 and explicit allow_mcp_only true; current Reminders UI does not display these stored text files')
    encoded=fields.get('data_base64')
    try:
        if not isinstance(encoded,str) or not 1<=len(encoded)<=4*((MAX_BYTES+2)//3):raise ValueError()
        raw=base64.b64decode(encoded,validate=True)
        if not 1<=len(raw)<=MAX_BYTES or base64.b64encode(raw).decode('ascii')!=encoded or b'\0' in raw:raise ValueError()
        raw.decode('utf-8')
    except (ValueError,UnicodeError,binascii.Error):
        raise SafetyError('Use 1..16384 bytes of UTF-8 text without NUL in canonical base64') from None
    return raw


def snapshot(adapter,item):
    source=item['source_id'];listing=item['list_id'];scope=list_scope(adapter,source,listing)
    graph=adapter.reader.read('list_order',list_id=listing);sections=adapter.reader.read('list_sections',list_id=listing)
    if (graph.get('source_id')!=source or graph.get('list_id')!=listing or not 1<=len(graph['nodes'])<=20
            or any(v['parent_id'] is not None for v in graph['nodes']) or sections.get('source_id')!=source
            or sections.get('list_id')!=listing or sections.get('sections')!=[]):
        raise SafetyError('File insertion requires a private unsectioned list of 1..20 incomplete nonrecurring root leaves')
    items=[]
    for node in graph['nodes']:
        value=adapter.snapshot({'operation':'update_reminder','source_id':source,'id':node['id'],'fields':{}})
        check_item_graph(value,graph,node['id'])
        if value['item'].get('completed') is not False or value['native_metadata'].get('child_ids')!=[]:
            raise SafetyError('File insertion requires complete incomplete root-leaf observations')
        observed=deepcopy(adapter.reader.read('get',id=node['id'],include_native_attachments=True)['item'])
        metadata=observed.pop('native_metadata',None)
        if (observed!=value['item'] or not isinstance(metadata,dict) or metadata.get('coverage')!=['attachments']
                or any(metadata.get(k)!=value['item'].get(k) for k in ('id','source_id','list_id'))
                or not isinstance(metadata.get('attachments'),list) or metadata.get('attachment_metadata_complete') is not False):
            raise SafetyError('Exact native attachment observations are unavailable or changed')
        value['attachments']=metadata;items.append(value)
    if (not any(v['item']['id']==item['id'] for v in items) or list_scope(adapter,source,listing)!=scope
            or adapter.reader.read('list_order',list_id=listing)!=graph or adapter.reader.read('list_sections',list_id=listing)!=sections):
        raise SafetyError('File target or source changed during review')
    value={**scope,'graph':graph,'sections':sections,'items':items}
    if len(canonical(value))>35000:raise SafetyError('File preservation review exceeds the fixed request bound')
    return value


def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','id','fields'} or change.get('operation')!='add_file_attachment'
            or change['source_id'] not in planning_access('reminders')['source_ids']):
        raise SafetyError('Select an exact reminder/source and text file fields')
    for key in ('source_id','id'):validate_id(change[key])
    raw=data(change['fields']);current=adapter.reader.read('get',id=change['id'])['item']
    if current.get('id')!=change['id'] or current.get('source_id')!=change['source_id']:raise SafetyError('File target is outside the selected source')
    item=deepcopy(change);item.update(list_id=current['list_id'],attachment_id=str(uuid.uuid4()).upper(),size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    item['before']=snapshot(adapter,item)
    selected=next(v for v in item['before']['items'] if v['item']['id']==item['id'])
    if selected['attachments']['attachments']!=[]:raise SafetyError('Initial file insertion requires an empty exposed attachment inventory')
    item.update(fidelity_verified=True,effects={
        'change':'Add one UTF-8 plain-text file with a server-assigned attachment ID to this exact existing private root reminder.',
        'file_preview':{'text':raw.decode('utf-8'),'size':len(raw),'sha256':item['sha256']},
        'preservation':'Verify bounded list membership/order, sections, all exposed task fields and attachment inventories. No existing attachment is replaced.',
        'interface':'MCP-only stored text file: the current Reminders app does not display it in the list or details panel. Do not use this as a visible app attachment or a complete backup.',
        'dispatch':'The SDK can materialize an Apple-managed temporary file before saving. Authorization and the mutation gate precede construction; subsequent failures remain uncertain even without a saved reminder.',
        'limits':'Original filename, hidden attachment completeness, cloud synchronization, notification delivery and arbitrary file types are unverified. No caller paths, download, attachment removal, cleanup or automatic replay.',
        'quota_objects':2})
    return item


def charge(item):
    raw=data(item['fields']);validate_id(item['attachment_id'])
    if len(raw)!=item['size'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise SafetyError('Reviewed file bytes changed')
    return 2


def record_matches(item,observed):
    try:
        if (item['operation']!='add_file_attachment' or observed.get('state')!='reminder_text_file_added'
                or any(observed.get(k)!=item[k] for k in ('id','source_id','attachment_id'))):return False
        before=item['before'];after=observed['snapshot']
        if any(after[k]!=before[k] for k in ('list','role','graph','sections')) or len(after['items'])!=len(before['items']):return False
        for old,new in zip(before['items'],after['items']):
            expected=deepcopy(old)
            if old['item']['id']==item['id']:
                if type(new['item'].get('modified')) not in (int,float) or not math.isfinite(new['item']['modified']):return False
                expected['item']['modified']=new['item']['modified']
                expected['attachments']['attachments']=[{'id':item['attachment_id'],'kind':'file','uti':'public.plain-text'}]
            if new!=expected:return False
        file=observed['file']
        return (charge(item)==2 and all(file.get(k)==item[k] for k in ('id','source_id','list_id','attachment_id','size','sha256'))
                and file.get('uti')=='public.plain-text' and file.get('kind')=='file' and file.get('data_mutated') is False)
    except (KeyError,TypeError,AttributeError,ValueError,SafetyError):return False


def expected_observation(adapter,item):
    value={'state':'reminder_text_file_added','id':item['id'],'source_id':item['source_id'],'attachment_id':item['attachment_id'],'snapshot':snapshot(adapter,item)}
    exported=export(item['id'],item['attachment_id'],MAX_BYTES,reader=adapter.reader)
    value['file']={k:exported[k] for k in ('id','source_id','list_id','attachment_id','size','sha256','uti','kind','data_mutated')}
    if not record_matches(item,value):raise SafetyError('Exact stored file bytes and otherwise preserved source did not verify')
    return value


def verify(adapter,item,outcome):
    if any(outcome.get(k)!=item[k] for k in ('id','source_id','list_id','attachment_id')) or outcome.get('saved_locally') is not True:return False
    expected_observation(adapter,item);return True
