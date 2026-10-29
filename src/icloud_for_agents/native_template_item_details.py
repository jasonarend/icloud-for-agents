"""Opted-in exact saved-template item text and uninterpreted native scheduling data."""
import base64,copy,hashlib,json,math,platform,sqlite3
from .core import SafetyError,digest
from . import native_reminder_template_store as store
from .notes_checklist_format import text_projection

CONTENT_REQUIRED=copy.deepcopy(store.REQUIRED)
CONTENT_REQUIRED['ZREMCDSAVEDREMINDER'].add('ZMETADATA')
MAX_METADATA=1048576
NATIVE_FIELDS={'creationDate','flagged','alarmTriggers','recurrenceRules'}


def invalid():raise SafetyError('Unsupported or incomplete saved-template item content; no inferred native semantics')


def _object(pairs):
    value={}
    for key,item in pairs:
        if key in value:invalid()
        value[key]=item
    return value


def _shape(value):
    pending=[(value,0)];budget=10000
    while pending:
        current,depth=pending.pop();budget-=1
        if budget<0 or depth>12:invalid()
        if isinstance(current,dict):
            if len(current)>100:invalid()
            for key,item in current.items():
                if not isinstance(key,str)or len(key)>1000:invalid()
                pending.append((key,depth+1));pending.append((item,depth+1))
        elif isinstance(current,list):
            if len(current)>1000:invalid()
            pending.extend((item,depth+1)for item in current)
        elif isinstance(current,str):
            try:
                if len(current.encode('utf-8',errors='strict'))>262144:invalid()
            except UnicodeError:invalid()
        elif current is not None and type(current)not in(bool,int,float):invalid()
        elif type(current)in(int,float)and(abs(current)>253402300799 or type(current)is float and not math.isfinite(current)):invalid()


def _document(value):
    if not isinstance(value,str)or not 1<=len(value)<=174764:invalid()
    try:raw=base64.b64decode(value,validate=True)
    except ValueError:invalid()
    if not 1<=len(raw)<=131072 or base64.b64encode(raw).decode()!=value:invalid()
    try:projected=text_projection(raw)
    except SafetyError:invalid()
    if len(projected['text'].encode())>20000:invalid()
    return projected,hashlib.sha256(raw).hexdigest()


def decode(raw,title,priority):
    if type(raw)is not bytes or not 2<=len(raw)<=MAX_METADATA or raw[0]!=1:invalid()
    try:value=json.loads(raw[1:].decode('utf-8',errors='strict'),object_pairs_hook=_object,parse_constant=lambda _:invalid())
    except(UnicodeError,ValueError,RecursionError):invalid()
    if not isinstance(value,dict):invalid()
    _shape(value)
    if value.get('title')!=title or type(value.get('priority'))is not int or value['priority']!=priority:invalid()
    title_doc,title_sha=_document(value.get('titleDocumentData'));notes_doc,notes_sha=_document(value.get('notesDocumentData'))
    if title_doc['text']!=title:invalid()
    native={key:value[key]for key in NATIVE_FIELDS if key in value}
    if len(json.dumps(native,ensure_ascii=False).encode())>65536:invalid()
    return {'title':title,'notes':notes_doc['text'],'priority':priority,'metadata_format_version':1,'text_document_format_version':title_doc['format_version'],
            'title_document_sha256':title_sha,'notes_document_sha256':notes_sha,'native_metadata_uninterpreted':native,
            'omitted_metadata_keys':sorted(set(value)-NATIVE_FIELDS-{'title','priority','titleDocumentData','notesDocumentData'}),
            'native_checklist_items_omitted':title_doc['native_checklist_items_omitted']+notes_doc['native_checklist_items_omitted'],
            'representation_complete':False,'restorable':False,'native_date_semantics':'uninterpreted_native_numeric_values_not_converted',
            'metadata_sha256':hashlib.sha256(raw).hexdigest()}


def _read(path,source,template,identity,admission):
    if identity not in {row['item_id']for row in admission['items']}:raise SafetyError('Saved item is outside the exact template outline')
    with store._readonly_store(path,CONTENT_REQUIRED)as(c,schema):
        if schema!=admission['schema_sha256']:raise SafetyError('Template store schema changed before its content read')
        mapping=dict(c.execute("SELECT Z_NAME,Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME IN ('REMCDAccount','REMCDTemplate','REMCDSavedReminder')"))
        if set(mapping)!={'REMCDAccount','REMCDTemplate','REMCDSavedReminder'}or len(set(mapping.values()))!=3:invalid()
        rows=c.execute('SELECT t.Z_OPT,s.Z_OPT,s.ZTITLE,s.ZPRIORITY,s.ZMETADATA FROM ZREMCDSAVEDREMINDER s JOIN ZREMCDTEMPLATE t ON s.ZTEMPLATE=t.Z_PK JOIN ZREMCDOBJECT a ON t.ZACCOUNT=a.Z_PK WHERE a.ZIDENTIFIER=? AND a.Z_ENT=? AND a.ZINACTIVE=0 AND a.ZMARKEDFORDELETION=0 AND t.ZIDENTIFIER=? AND t.Z_ENT=? AND t.ZPARENTACCOUNT=a.Z_PK AND t.ZMARKEDFORDELETION=0 AND s.ZIDENTIFIER=? AND s.Z_ENT=? AND s.ZACCOUNT=a.Z_PK AND s.ZMARKEDFORDELETION=0 LIMIT 2',
            (store.identity(source),mapping['REMCDAccount'],store.identity(template),mapping['REMCDTemplate'],store.identity(identity),mapping['REMCDSavedReminder'])).fetchall()
        if len(rows)!=1:raise SafetyError('Exact live saved-item content relationship is absent or ambiguous')
        revision,item_revision,title,priority,raw=rows[0];known=next(v for v in admission['items']if v['item_id']==identity)
        if revision!=admission['template_revision']or not store.integer(item_revision)or title!=known['title']or priority!=known['priority']:raise SafetyError('Saved-item metadata changed before its content read')
        projected=decode(raw,title,priority)
        return projected|{'template_revision':revision,'item_revision':item_revision,'schema_sha256':schema,'parent_item_id':known['parent_item_id']}


def _observation(source,template,identity):
    matches=[]
    for path in store.paths():
        before=store.inspect_store(path,source,template)
        if before is None:continue
        info=store.owned(path);value=_read(path,source,template,identity,before);after=store.inspect_store(path,source,template);current=store.owned(path)
        if after!=before or(info.st_dev,info.st_ino)!=(current.st_dev,current.st_ino):raise SafetyError('Template store or outline changed during content observation')
        matches.append(value)
    if len(matches)!=1:raise SafetyError('Selected template content does not map to one local account store')
    return matches[0]


def inspect(source_id,template_id,item_id,*,reader=None):
    from .native_once import selected_read_access
    from .native_read import NativeReader,native_config,validate_template_sections
    for value in(source_id,template_id,item_id):store.identity(value)
    access=selected_read_access('reminders');config=native_config('reminders')
    if source_id not in config.get('source_ids',[])or config.get('metadata_backend')!='reminders-store-readonly'or config.get('advanced_backend')!='reminderkit'or not config.get('helper_path'):raise SafetyError('Saved-template content requires exact selected source and explicit optional ReminderKit/read-only store access')
    if platform.system()!='Darwin'or platform.mac_ver()[0].split('.')[0]!='27':raise SafetyError('Saved-template content layout is verified only on macOS 27')
    reader=reader or NativeReader('reminders');before=reader.read('template_sections',source_id=source_id,id=template_id);validate_template_sections(before,source_id,template_id)
    try:
        value=_observation(source_id,template_id,item_id)
        if _observation(source_id,template_id,item_id)!=value:raise SafetyError('Saved-template item changed during repeated content read')
    except(OSError,sqlite3.Error,ValueError):raise SafetyError('Optional saved-template content is unavailable; no fallback or database write')from None
    if reader.read('template_sections',source_id=source_id,id=template_id)!=before or selected_read_access('reminders')!=access or native_config('reminders')!=config:raise SafetyError('Template scope or access changed during content read')
    return {'protocol_version':1,'source_id':source_id,'template_id':template_id,'item_id':item_id,**value,'snapshot_sha256':digest(value),
            'data_mutated':False,'database_writes':False,'saved_local_state':True,'unsaved_ui_state_verified':False,'instructions_trusted':False,
            'item_kind':'template_saved_item_not_live_reminder','sync_verified':False,'backend':'reminders-store-template-item-details-v1',
            'coverage':'Selected saved-item text projection and uninterpreted native scheduling/flag values; rich objects, attachments and native editing are not reconstructed'}
