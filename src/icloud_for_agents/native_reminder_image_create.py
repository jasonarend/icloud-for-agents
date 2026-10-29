"""Exact PNG image insertion with bounded input and independent stored-byte proof."""
import base64
import binascii
from copy import deepcopy
import hashlib
import math
import uuid
from .core import SafetyError
from .native_once import planning_access
from .native_reminder_files import validate_id,export
from .native_reminder_file_create import snapshot
from .png_format import dimensions,MAX_BYTES

COVERAGE='private_ordinary_list_bounded_root_tasks_order_sections_and_exposed_image_attachment_inventories'
RECOVERY_EFFECT='Compare only reviewed list/tasks/order, exposed attachment inventories and locally available exact PNG bytes. Unchanged task observations do not exclude Apple-managed temporary files, delayed effects or hidden records. No cleanup, rollback or replay.'


def data(fields):
    try:
        if not isinstance(fields,dict) or set(fields)!={'data_base64'}:raise ValueError()
        encoded=fields['data_base64']
        if not isinstance(encoded,str) or not 1<=len(encoded)<=4*((MAX_BYTES+2)//3):raise ValueError()
        raw=base64.b64decode(encoded,validate=True)
        if base64.b64encode(raw).decode('ascii')!=encoded:raise ValueError()
        width,height=dimensions(raw)
    except (ValueError,UnicodeError,binascii.Error):
        raise SafetyError('Supply only canonical data_base64 of one complete RGB/RGBA 8-bit noninterlaced PNG, at most 16KiB and 1024x1024; no metadata, animation or paths') from None
    return raw,width,height


def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','id','fields'} or change.get('operation')!='add_image_attachment'
            or change['source_id'] not in planning_access('reminders')['source_ids']):raise SafetyError('Select an exact reminder/source and PNG data_base64')
    for key in ('source_id','id'):validate_id(change[key])
    raw,width,height=data(change['fields']);current=adapter.reader.read('get',id=change['id'])['item']
    if current.get('id')!=change['id'] or current.get('source_id')!=change['source_id']:raise SafetyError('Image target is outside the selected source')
    item=deepcopy(change);item.update(list_id=current['list_id'],attachment_id=str(uuid.uuid4()).upper(),size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),width=width,height=height)
    item['before']=snapshot(adapter,item);selected=next(v for v in item['before']['items'] if v['item']['id']==item['id'])
    if selected['attachments']['attachments']!=[]:raise SafetyError('Initial image insertion requires an empty exposed attachment inventory')
    item.update(fidelity_verified=True,effects={
        'change':'Add one PNG image with a server-assigned native attachment ID to this exact existing private root reminder.',
        'image_preview':{'media_type':'image/png','width':width,'height':height,'size':len(raw),'sha256':item['sha256']},
        'preservation':'Verify the private ordinary list, bounded root-task fields/order, empty sections, bystanders and exposed attachment inventories. No existing image is replaced.',
        'dispatch':'The SDK can materialize an Apple-managed temporary file before saving. Authorization and the mutation gate precede construction; later failures remain uncertain even without a database save.',
        'limits':'No original filename, cloud synchronization or unexposed metadata guarantee. JPEG, metadata-bearing PNG, animation, arbitrary files, replacement, removal and automatic replay are unavailable.',
        'quota_objects':2})
    return item


def charge(item):
    raw,width,height=data(item['fields']);validate_id(item['attachment_id'])
    if len(raw)!=item['size'] or hashlib.sha256(raw).hexdigest()!=item['sha256'] or (width,height)!=(item['width'],item['height']):raise SafetyError('Reviewed PNG bytes or dimensions changed')
    return 2


def record_matches(item,observed):
    try:
        if (item['operation']!='add_image_attachment' or observed.get('state')!='reminder_png_image_added'
                or any(observed.get(k)!=item[k] for k in ('id','source_id','attachment_id'))):return False
        before=item['before'];after=observed['snapshot']
        if any(after[k]!=before[k] for k in ('list','role','graph','sections')) or len(after['items'])!=len(before['items']):return False
        for old,new in zip(before['items'],after['items']):
            expected=deepcopy(old)
            if old['item']['id']==item['id']:
                if type(new['item'].get('modified')) not in (int,float) or not math.isfinite(new['item']['modified']):return False
                expected['item']['modified']=new['item']['modified'];expected['attachments']['attachments']=[{'id':item['attachment_id'],'kind':'image','uti':'public.png'}]
            if new!=expected:return False
        file=observed['file']
        return (charge(item)==2 and all(file.get(k)==item[k] for k in ('id','source_id','list_id','attachment_id','size','sha256','width','height'))
                and file.get('uti')=='public.png' and file.get('kind')=='image' and file.get('data_mutated') is False)
    except (KeyError,TypeError,AttributeError,ValueError,SafetyError):return False


def expected_observation(adapter,item):
    value={'state':'reminder_png_image_added','id':item['id'],'source_id':item['source_id'],'attachment_id':item['attachment_id'],'snapshot':snapshot(adapter,item)}
    exported=export(item['id'],item['attachment_id'],MAX_BYTES,reader=adapter.reader)
    try:width,height=dimensions(base64.b64decode(exported['data'],validate=True))
    except (KeyError,ValueError,binascii.Error):raise SafetyError('Stored native PNG image did not verify') from None
    value['file']={k:exported[k] for k in ('id','source_id','list_id','attachment_id','size','sha256','uti','kind','data_mutated')}|{'width':width,'height':height}
    if not record_matches(item,value):raise SafetyError('Exact stored PNG bytes/dimensions and preserved source did not verify')
    return value


def verify(adapter,item,outcome):
    if any(outcome.get(k)!=item[k] for k in ('id','source_id','list_id','attachment_id')) or outcome.get('saved_locally') is not True:return False
    expected_observation(adapter,item);return True
