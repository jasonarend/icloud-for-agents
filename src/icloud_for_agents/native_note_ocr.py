"""Derived on-device OCR of one exact scoped Notes PNG/JPEG export.

Native Notes OCR/handwriting/index state is not read or reconstructed. No writer,
caller image bytes, arbitrary paths, new runtime dependencies or permissions.
"""
import base64,json,math,os,subprocess,tempfile
from .core import SafetyError
from .native_read import NativeReader,native_config
from .native_once import selected_read_access
from .native_app import bundle_fingerprint,image_processor
from .native_note_attachments import validate_request,validate_export
MAX_BYTES=131072
KEYS={'protocol_version','source_id','image_sha256','size','image_type','width','height','orientation','engine','request_revision','language','language_correction','lines','truncated','derived','native_notes_ocr','data_mutated','order'}


def _access():
    status=selected_read_access('notes');config=native_config('notes')
    if not config.get('helper_path'):raise SafetyError('Image OCR requires an updated configured native helper')
    image_processor(config['helper_path'])
    return config,status['effective_access'],bundle_fingerprint(config['helper_path'])


def process_request(request,config):
    """Fixed private stdio processing; no Notes authority, files or credentials."""
    if config!=native_config('notes') or request.get('source_id') not in config.get('source_ids',[]):raise SafetyError('Image OCR source/configuration changed')
    processor=image_processor(config.get('helper_path',''));encoded=json.dumps(request,ensure_ascii=False).encode()
    if len(encoded)>240000:raise SafetyError('Image OCR request exceeds its byte bound')
    env={k:os.environ[k]for k in ('HOME','PATH','TMPDIR','LANG')if k in os.environ}
    try:
        with tempfile.TemporaryFile()as output,tempfile.TemporaryFile()as errors:
            result=subprocess.run([str(processor)],input=encoded,stdout=output,stderr=errors,timeout=65,check=False,env=env,close_fds=True)
            if result.returncode:raise SafetyError('Image OCR processor failed; no automatic retry')
            output.seek(0);raw=output.read(131073)
        if len(raw)>131072:raise SafetyError('Image OCR response exceeds its byte bound')
        value=json.loads(raw)
    except (subprocess.SubprocessError,OSError,ValueError):raise SafetyError('Image OCR processor did not return a bounded result; no automatic retry')from None
    if not isinstance(value,dict)or type(value.get('protocol_version'))is not int or value.get('protocol_version')!=1 or 'error'in value:raise SafetyError('Image OCR processor refused or timed out; no automatic retry')
    return value


def _validate(value,exported,image_type):
    value={k:v for k,v in value.items()if k!='request_id'}if isinstance(value,dict)else value
    if (not isinstance(value,dict)or set(value)!=KEYS or type(value['protocol_version'])is not int or value['protocol_version']!=1
            or value['source_id']!=exported['source_id']or value['image_sha256']!=exported['sha256']or type(value['size'])is not int or value['size']!=exported['size']
            or value['image_type']!=image_type or value['engine']!='Apple_Vision'or type(value['request_revision'])is not int or value['request_revision']!=3
            or value['language']!='en-US'or value['language_correction']is not False or value['derived']is not True or value['native_notes_ocr']is not False
            or value['data_mutated']is not False or value['order']!='Vision_observation_order_not_document_layout'or type(value['truncated'])is not bool
            or type(value['width'])is not int or type(value['height'])is not int or not 1<=value['width']<=8192 or not 1<=value['height']<=8192 or value['width']*value['height']>8_000_000
            or type(value['orientation'])is not int or not 1<=value['orientation']<=8 or not isinstance(value['lines'],list)or len(value['lines'])>50):raise SafetyError('Exact bounded image OCR response did not verify')
    total=0
    for line in value['lines']:
        if (not isinstance(line,dict)or set(line)!={'text','confidence','bounding_box'}or not isinstance(line['text'],str)
                or type(line['confidence'])not in (int,float)or not math.isfinite(line['confidence'])or not 0<=line['confidence']<=1
                or not isinstance(line['bounding_box'],dict)or set(line['bounding_box'])!={'x','y','width','height'}):raise SafetyError('Invalid OCR text, confidence or geometry')
        try:length=len(line['text'].encode('utf-8',errors='strict'))
        except UnicodeError:raise SafetyError('Invalid OCR text encoding')from None
        total+=length
        box=line['bounding_box']
        if (length>2000 or total>20000 or any(type(v)not in (int,float)or not math.isfinite(v)or not 0<=v<=1 for v in box.values())
                or box['x']+box['width']>1.000001 or box['y']+box['height']>1.000001):raise SafetyError('OCR response exceeds text or normalized image bounds')
    return value


def recognize(folder_id,identity,attachment_id,*,max_bytes=MAX_BYTES,reader=None):
    fields={'folder_id':folder_id,'id':identity,'attachment_id':attachment_id,'max_bytes':max_bytes}
    validate_request(fields)
    if max_bytes>MAX_BYTES:raise SafetyError('Image OCR input is bounded to 131072 exported bytes')
    access=_access();reader=reader or NativeReader('notes');config=access[0]
    before=reader.read('get',folder_id=folder_id,id=identity)
    if (before.get('item',{}).get('id')!=identity or before['item'].get('folder_id')!=folder_id
            or before['item'].get('source_id')not in config['source_ids']or before['item'].get('locked')is not False):raise SafetyError('Image OCR requires the exact unlocked note in its selected source/folder')
    exported=validate_export(reader.read('attachment_export',**fields),fields,config['source_ids'])
    if exported['source_id']!=before['item']['source_id']:raise SafetyError('OCR attachment and note sources disagree')
    raw=base64.b64decode(exported['data'],validate=True)
    image_type='public.png'if raw.startswith(b'\x89PNG\r\n\x1a\n')else'public.jpeg'if raw.startswith(b'\xff\xd8\xff')else None
    if image_type is None:raise SafetyError('Image OCR supports exported PNG/JPEG files only')
    if _access()!=access:raise SafetyError('Notes access or helper changed before image processing')
    request={'protocol_version':1,'service':'notes','action':'ocr_image','source_ids':[exported['source_id']],'source_id':exported['source_id'],'image_data':exported['data'],'image_sha256':exported['sha256']}
    result=_validate(reader._dispatch(request,config),exported,image_type)
    if (reader.read('attachment_export',**fields)!=exported or reader.read('get',folder_id=folder_id,id=identity)!=before or _access()!=access):raise SafetyError('OCR source bytes, note, access or helper changed during recognition')
    return result|{'folder_id':folder_id,'note_id':identity,'attachment_id':attachment_id,'complete_backup':False,'handwriting_verified':False,
                   'coverage':'Derived printed-image text only; confidence is not certainty, native Notes OCR/index/handwriting and layout are not reconstructed',
                   'consistency':'separate_live_reads_may_change'}
