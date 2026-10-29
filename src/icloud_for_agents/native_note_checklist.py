"""Opted-in exact unlocked-note checklist reads; no native model or DB writes."""
import hashlib
import sqlite3
from .core import SafetyError,digest
from .native_note_assets import _access,_current,_state,_unlocked
from .native_note_store import readonly_store,parse_identity
from .native_read import NativeReader
from .notes_checklist_format import decode,MAX_COMPRESSED

# This one projection reads a content blob. The ordinary metadata connection
# continues to deny ZICNOTEDATA; no generic query/path interface is introduced.
COLUMNS={'ZICCLOUDSYNCINGOBJECT':{'Z_PK','Z_ENT','Z_OPT','ZFOLDER','ZMARKEDFORDELETION','ZNOTEDATA','ZISPASSWORDPROTECTED'},
         'ZICNOTEDATA':{'Z_PK','Z_ENT','Z_OPT','ZNOTE','ZDATA','ZCRYPTOINITIALIZATIONVECTOR','ZCRYPTOTAG'},
         'Z_PRIMARYKEY':{'Z_NAME','Z_ENT'},'Z_METADATA':{'Z_UUID'}}


def _authorize(action,table,column,database,trigger):
    allowed=action in {sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION}
    if action==sqlite3.SQLITE_READ:allowed=(table in COLUMNS and column in COLUMNS[table])or table in {'sqlite_master','sqlite_schema'}
    if action==sqlite3.SQLITE_PRAGMA:allowed=table=='table_info'and column in COLUMNS
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def _blob(folder_id,identity):
    state,access=_state(folder_id,identity);store,note=parse_identity(identity,'ICNote');_,folder=parse_identity(folder_id,'ICFolder')
    try:
        with readonly_store() as (db,_):
            db.set_authorizer(_authorize);db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_COMPRESSED+4096)
            for table,required in COLUMNS.items():
                if db.execute('SELECT type FROM sqlite_master WHERE name=?',(table,)).fetchone()!=('table',):raise SafetyError('Unknown native checklist storage table')
                columns={row[1] for row in db.execute('PRAGMA table_info('+table+')')}
                if not required<=columns:raise SafetyError('Native Notes schema lacks exact checklist content relations')
            uuids=db.execute('SELECT Z_UUID FROM Z_METADATA LIMIT 2').fetchall()
            entities=db.execute("SELECT Z_NAME,Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME IN ('ICNote','ICNoteData')").fetchall();mapping=dict(entities)
            if (len(uuids)!=1 or not isinstance(uuids[0][0],str) or uuids[0][0].lower()!=store
                    or len(entities)!=2 or set(mapping)!={'ICNote','ICNoteData'} or any(type(v)is not int or v<1 for v in mapping.values())):raise SafetyError('Native checklist store/entities cannot be verified')
            rows=db.execute('SELECT d.Z_PK,d.Z_ENT,d.Z_OPT,d.ZNOTE,d.ZDATA,d.ZCRYPTOINITIALIZATIONVECTOR IS NOT NULL,d.ZCRYPTOTAG IS NOT NULL,n.Z_ENT,n.Z_OPT,n.ZFOLDER,n.ZMARKEDFORDELETION,n.ZNOTEDATA,n.ZISPASSWORDPROTECTED FROM ZICNOTEDATA d JOIN ZICCLOUDSYNCINGOBJECT n ON d.ZNOTE=n.Z_PK WHERE n.Z_PK=? LIMIT 2',(note,)).fetchall()
            if len(rows)!=1:raise SafetyError('Exact note has no unique native content row')
            pk,entity,revision,parent,data,iv,tag,nentity,nrevision,nfolder,deleted,relation,protected=rows[0]
            if (type(pk)is not int or pk<1 or entity!=mapping['ICNoteData'] or type(revision)is not int or revision<0
                    or parent!=note or nentity!=mapping['ICNote'] or nrevision!=state['note_revision'] or nfolder!=folder or deleted!=0 or relation!=pk
                    or protected not in (None,0) or iv!=0 or tag!=0 or type(data)is not bytes or not 1<=len(data)<=MAX_COMPRESSED):raise SafetyError('Native checklist content identity, unlocked state or scope changed')
    except sqlite3.Error:raise SafetyError('Native checklist content unavailable or exceeds its fixed bound; no fallback') from None
    if _state(folder_id,identity)[0]!=state:raise SafetyError('Note changed during native checklist content read')
    _current(access)
    return state,{'content_row_id':pk,'content_revision':revision,'compressed_sha256':hashlib.sha256(data).hexdigest()},data


def inventory(folder_id,identity,*,limit=20,offset=0,reader=None):
    if type(limit)is not int or not 1<=limit<=20 or type(offset)is not int or not 0<=offset<=1000:raise SafetyError('Use checklist pages of 1..20 items and offset 0..1000')
    access=_access();reader=reader or NativeReader('notes');before=_unlocked(reader,folder_id,identity,access[0]['source_ids'])
    if before['item'].get('truncated') is not False or not isinstance(before['item'].get('text'),str):raise SafetyError('Native checklist guard requires complete bounded app text')
    state,content,data=_blob(folder_id,identity)
    if state['source_id']!=before['item']['source_id']:raise SafetyError('Native checklist and app source observations disagree')
    value=decode(data)
    if value['text_sha256']!=hashlib.sha256(before['item']['text'].encode()).hexdigest():raise SafetyError('Native checklist text and current app text disagree')
    if (_blob(folder_id,identity)!=(state,content,data)or reader.read('get',folder_id=folder_id,id=identity)!=before):raise SafetyError('Native checklist or app note changed during observation')
    _current(access);items=value['items'];total=len(items);page=items[offset:offset+limit];done=offset+len(page)>=total
    return {'protocol_version':1,'id':identity,'folder_id':folder_id,'source_id':state['source_id'],'note_revision':state['note_revision'],
            'format_version':value['format_version'],'snapshot_sha256':digest({'native':state,'content':content,'projection':value}),
            'items':page,'total':total,'checked':sum(v['checked']for v in items),'offset':offset,'next_offset':None if done else offset+len(page),'done':done,
            'representation_complete':False,'coverage':value['coverage'],'native_body_blob_read':True,'data_mutated':False,
            'saved_local_state':True,'unsaved_ui_state_verified':False,
            'backend':'notes-store-checklist-v1','consistency':'separate_live_reads_may_change'}
