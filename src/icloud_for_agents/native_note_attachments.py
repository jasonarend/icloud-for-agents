"""Bounded native-file exports; callers cannot supply a filesystem destination."""
import base64,binascii,hashlib
from .core import SafetyError
MAX_BYTES=1048576

def validate_request(fields):
    if (set(fields)-{'folder_id','id','attachment_id','max_bytes'} or any(not isinstance(fields.get(key),str) or not 1<=len(fields[key])<=1000 for key in ('folder_id','id','attachment_id'))
            or type(fields.get('max_bytes',MAX_BYTES)) is not int or not 1<=fields.get('max_bytes',MAX_BYTES)<=MAX_BYTES):raise SafetyError('Export requires exact folder/note/attachment IDs and max_bytes from 1 to 1048576; no file paths')

def validate_export(value,fields,sources):
    keys={'protocol_version','source_id','folder_id','note_id','attachment_id','name','content_identifier','modified','encoding','data','size','sha256','data_mutated','representation','complete_backup'}
    if (not isinstance(value,dict) or set(value)!=keys or type(value.get('protocol_version')) is not int or value.get('protocol_version')!=1 or value.get('source_id') not in sources
            or any(value.get(target)!=fields[key] for key,target in (('folder_id','folder_id'),('id','note_id'),('attachment_id','attachment_id')))
            or value.get('encoding')!='base64' or value.get('representation')!='Notes_scripting_native_file' or value.get('data_mutated') is not False or value.get('complete_backup') is not False
            or type(value.get('size')) is not int or not 0<=value['size']<=fields.get('max_bytes',MAX_BYTES)
            or not isinstance(value.get('data'),str) or len(value['data'])>4*((fields.get('max_bytes',MAX_BYTES)+2)//3)
            or any(value.get(k) is not None and (not isinstance(value[k],str) or len(value[k])>1000) for k in ('name','content_identifier','modified'))):raise SafetyError('Exact scoped bounded native attachment export did not verify')
    try:raw=base64.b64decode(value['data'],validate=True)
    except (ValueError,binascii.Error):raise SafetyError('Attachment export encoding is invalid') from None
    if len(raw)!=value['size'] or hashlib.sha256(raw).hexdigest()!=value.get('sha256'):raise SafetyError('Attachment export bytes do not match the reported size/hash')
    return value
