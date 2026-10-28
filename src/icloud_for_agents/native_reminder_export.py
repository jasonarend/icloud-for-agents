"""Bounded, read-only JSON projection; not an import or a backup."""
import json
from .core import SafetyError

FIELDS=('id','source_id','list_id','title','notes','completed','priority','due','start',
        'created','modified','completion_date','url','location','has_recurrence',
        'has_alarms','recurrence_rules','alarms','public_metadata_complete',
        'content_complete','omitted_features')
MAX_BYTES=1_000_000

def export_page(reader, list_id, limit=20, offset=0):
    if not isinstance(list_id,str) or not 1<=len(list_id)<=1000:
        raise SafetyError('Select one exact reminder list for export')
    if type(limit) is not int or not 1<=limit<=20 or type(offset) is not int or not 0<=offset<=100000:
        raise SafetyError('Export pages require a limit of 1..20 and a bounded offset')
    page=reader.read('query',list_id=list_id,limit=limit,offset=offset)
    items=page['items']; projected=[]
    for item in items:
        if (not isinstance(item,dict) or item.get('list_id')!=list_id
                or not all(key in item for key in FIELDS)
                or item['content_complete'] is not False
                or type(item['public_metadata_complete']) is not bool
                or not isinstance(item['omitted_features'],list)):
            raise SafetyError('Reminder export requires the current bounded public projection')
        projected.append({key:item[key] for key in FIELDS})
    result={'format':'icloud-for-agents-reminders-observation','version':1,'list_id':list_id,
            'items':projected,'page':{'offset':offset,'next_offset':page['next_offset'],'done':page['done'],
                    'consistency':'live_offset_may_shift'},
            'coverage':{'scope':'one requested list within selected sources; one live page', 'list_observed_in_page':bool(projected), 'atomic_snapshot':False,
                'complete_backup':False,'restorable':False,'native_metadata_included':False,
                'limitations':['Pages may shift or change between reads; an empty page does not establish that the requested list exists.','Native tags, hierarchy, attachments and advanced metadata are omitted.',
                               'Public recurrence and alarm observations may be incomplete; inspect each item.',
                               'Identifiers are provider-specific and may not survive recreation.']}}
    try:size=len(json.dumps(result,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('utf-8'))
    except (TypeError,ValueError,UnicodeError):raise SafetyError('Reminder export contains unsupported values') from None
    if size>MAX_BYTES:raise SafetyError('Reminder export exceeds one million bytes; reduce the page size')
    return result
