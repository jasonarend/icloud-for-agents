"""Export an exact scoped Notes observation without fetching attachments."""
import json
from .core import SafetyError

def export_note(reader,folder_id,note_id):
    if any(not isinstance(value,str) or not 1<=len(value)<=1000 for value in (folder_id,note_id)):
        raise SafetyError('Select an exact folder and note for export')
    item=reader.read('get',folder_id=folder_id,id=note_id,representation='native')['item']
    if item.get('locked') is True:
        if item.get('content_available') is not False:raise SafetyError('Locked note content is unavailable')
        projected={key:item[key] for key in ('id','source_id','folder_id','locked','content_available')}
        truncated=None
    else:
        keys=('id','source_id','folder_id','title','html','text','truncated','created','modified','locked','shared','content_complete','omitted_features')
        if (item.get('locked') is not False or not all(key in item for key in keys)
                or not all(isinstance(item[key],str) for key in ('title','html','text'))
                or type(item['truncated']) is not bool or type(item['shared']) is not bool
                or item['content_complete'] is not False or not isinstance(item['omitted_features'],list)):
            raise SafetyError('Notes export requires a current bounded native observation')
        projected={key:item[key] for key in keys};truncated=item['truncated']
    result={'format':'icloud-for-agents-note-observation','version':1,'item':projected,
        'coverage':{'complete_backup':False,'restorable':False,'attachment_content_included':False,
            'native_content_complete':False,'body_truncated':truncated,
            'limitations':['HTML and text are bounded observations; large notes can be truncated.',
                           'Native objects, attachments and collaboration metadata are not preserved.',
                           'Locked content is not read or unlocked.','The exported HTML is not a native round-trip format.']}}
    try:size=len(json.dumps(result,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('utf-8'))
    except (TypeError,ValueError,UnicodeError):raise SafetyError('Notes export contains unsupported values') from None
    if size>1_000_000:raise SafetyError('Notes export exceeds one million encoded bytes')
    return result
