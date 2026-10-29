"""Opted-in fixed-query template outlines; no bodies, content blobs or database writes."""
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import re
import sqlite3
import stat
import uuid
from .core import SafetyError, digest

REQUIRED = {
    'ZREMCDOBJECT': {'Z_PK','Z_ENT','ZIDENTIFIER','ZINACTIVE','ZMARKEDFORDELETION'},
    'ZREMCDTEMPLATE': {'Z_PK','Z_ENT','Z_OPT','ZIDENTIFIER','ZACCOUNT','ZPARENTACCOUNT','ZMARKEDFORDELETION','ZNAME'},
    'ZREMCDSAVEDREMINDER': {'Z_ENT','Z_OPT','ZACCOUNT','ZTEMPLATE','ZIDENTIFIER','ZPARENTSAVEDREMINDERIDENTIFIER',
                         'ZTITLE','ZPRIORITY','ZHASHASHTAGS','ZHASLOCATIONTRIGGERSORVEHICLEEVENTTRIGGERS','ZMARKEDFORDELETION'},
    'Z_PRIMARYKEY': {'Z_ENT','Z_NAME'}, 'Z_METADATA': {'Z_UUID'}}


def identity(value):
    try:
        if not isinstance(value,str) or str(uuid.UUID(value)).upper()!=value:raise ValueError()
        return uuid.UUID(value).bytes
    except (ValueError,AttributeError):
        raise SafetyError('Use exact canonical discovered Reminders source and template IDs') from None


def stores_root():
    return Path.home()/'Library/Group Containers/group.com.apple.reminders/Container_v1/Stores'


def owned(path, directory=False):
    info=path.lstat()
    if ((not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
            or info.st_uid!=os.getuid() or info.st_mode&0o022):
        raise SafetyError('Reminders store paths must be real, owned and not writable by other users')
    return info


def paths():
    root=stores_root()
    for path in (root.parent.parent,root.parent,root):owned(path,True)
    result=[];entries=0
    with os.scandir(root) as values:
        for entry in values:
            entries+=1
            if entries>200:raise SafetyError('Reminders store directory exceeds the verified discovery bound')
            if not entry.name.endswith('.sqlite'):continue
            match=re.fullmatch(r'Data-([0-9A-Fa-f-]{36})\.sqlite',entry.name)
            if not match:raise SafetyError('Unknown Reminders store filename; no guessed fallback')
            uuid.UUID(match[1]);path=root/entry.name;owned(path);result.append(path)
    if not 1<=len(result)<=20:raise SafetyError('Reminders store discovery is unavailable or exceeds the verified bound')
    return sorted(result)


def readonly_store(path):
    return _readonly_store(path,REQUIRED)


@contextmanager
def _readonly_store(path,required):
    for value in (path,Path(str(path)+'-wal'),Path(str(path)+'-shm')):
        try:owned(value)
        except FileNotFoundError:
            if value==path:raise
    before=owned(path)
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3,isolation_level=None)
    try:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,1048576)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,10000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED,0)
        connection.execute('PRAGMA query_only=ON');connection.execute('PRAGMA trusted_schema=OFF')
        def authorize(action,first,second,database,trigger):
            allowed=action in {sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION}
            if action==sqlite3.SQLITE_READ:
                allowed=first in {'sqlite_master','sqlite_schema'} or (first in required and second in required[first])
            if action==sqlite3.SQLITE_PRAGMA:allowed=first=='table_info' and second in required
            return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize);remaining=[1000]
        def progress():remaining[0]-=1;return remaining[0]<=0
        connection.set_progress_handler(progress,1000);connection.execute('BEGIN');schema={}
        for table,columns in required.items():
            if connection.execute('SELECT type FROM sqlite_master WHERE name=?',(table,)).fetchone()!=('table',):
                raise SafetyError('Unknown Reminders store schema; no inferred tables or views')
            schema[table]=[row[1:3] for row in connection.execute('PRAGMA table_info('+table+')')]
            if not columns<={row[0] for row in schema[table]}:raise SafetyError('Reminders store schema lacks verified identity or scope columns')
        after=owned(path)
        if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):raise SafetyError('Reminders store changed during admission')
        yield connection,digest(schema)
    finally:connection.close()


def integer(value,minimum=0):
    return type(value) is int and minimum<=value<=2147483647


def text(value):
    return (isinstance(value,str) and len(value)<=1000 and not any(0xD800<=ord(c)<=0xDFFF for c in value)
            and len(value.encode())<=4000)


def stored_id(value):
    if not isinstance(value,bytes) or len(value)!=16:raise SafetyError('Unknown native UUID storage; no inferred identifier conversion')
    return str(uuid.UUID(bytes=value)).upper()


def inspect_store(path,source,template):
    with readonly_store(path) as (connection,schema):
        store=connection.execute('SELECT Z_UUID FROM Z_METADATA LIMIT 2').fetchall()
        if len(store)!=1 or not isinstance(store[0][0],str):raise SafetyError('Ambiguous Reminders native store identity')
        uuid.UUID(store[0][0])
        records=connection.execute("SELECT Z_NAME,Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME IN ('REMCDAccount','REMCDTemplate','REMCDSavedReminder')").fetchall()
        entities=dict(records)
        if len(records)!=3 or len(entities)!=3 or len(set(entities.values()))!=3 or any(not integer(v,1) for v in entities.values()):
            raise SafetyError('Native Reminders template entity mapping is unavailable')
        accounts=connection.execute('SELECT Z_PK,Z_ENT,ZINACTIVE,ZMARKEDFORDELETION FROM ZREMCDOBJECT WHERE ZIDENTIFIER=? AND Z_ENT=? LIMIT 2',
                                    (identity(source),entities['REMCDAccount'])).fetchall()
        if not accounts:return None
        if len(accounts)!=1 or not integer(accounts[0][0],1) or accounts[0][2:]!=(0,0):
            raise SafetyError('Selected Reminders account is ambiguous, inactive or deleted')
        account=accounts[0][0]
        rows=connection.execute('SELECT Z_PK,Z_ENT,Z_OPT,ZACCOUNT,ZPARENTACCOUNT,ZMARKEDFORDELETION,ZNAME FROM ZREMCDTEMPLATE WHERE ZIDENTIFIER=? LIMIT 2',
                                (identity(template),)).fetchall()
        if (len(rows)!=1 or not integer(rows[0][0],1) or rows[0][1]!=entities['REMCDTemplate'] or not integer(rows[0][2])
                or rows[0][3:6]!=(account,account,0) or not text(rows[0][6])):
            raise SafetyError('Template is absent, deleted, ambiguous or outside the selected account')
        template_pk,_,revision,_,_,_,title=rows[0]
        values=connection.execute('SELECT Z_ENT,Z_OPT,ZACCOUNT,ZTEMPLATE,ZIDENTIFIER,ZPARENTSAVEDREMINDERIDENTIFIER,ZTITLE,ZPRIORITY,ZHASHASHTAGS,ZHASLOCATIONTRIGGERSORVEHICLEEVENTTRIGGERS,ZMARKEDFORDELETION FROM ZREMCDSAVEDREMINDER WHERE ZTEMPLATE=? LIMIT 201',
                                  (template_pk,)).fetchall()
        if len(values)>200:raise SafetyError('Template exceeds the verified 200-item outline bound')
        items=[];seen=set()
        for entity,item_revision,owner,parent_template,item_id,parent_id,label,priority,tags,triggers,deleted in values:
            if (entity!=entities['REMCDSavedReminder'] or not integer(item_revision) or owner!=account or parent_template!=template_pk
                    or deleted not in (0,1) or not text(label) or type(priority) is not int or not 0<=priority<=9
                    or tags not in (None,0,1) or triggers not in (None,0,1)):
                raise SafetyError('Template item metadata or account relationship is incomplete')
            item_id=stored_id(item_id)
            if item_id in seen:raise SafetyError('Duplicate template item identity')
            seen.add(item_id)
            if deleted:continue
            items.append({'item_id':item_id,'parent_item_id':None if parent_id is None else stored_id(parent_id),'title':label,'priority':priority,
                          'has_tags':None if tags is None else bool(tags),'has_location_or_vehicle_triggers':None if triggers is None else bool(triggers)})
        parents={v['item_id']:v['parent_item_id'] for v in items}
        for item in items:
            current=item['item_id'];ancestors=set()
            while current is not None:
                if current not in parents or current in ancestors:raise SafetyError('Template hierarchy is incomplete or cyclic')
                ancestors.add(current);current=parents[current]
        return {'title':title,'items':sorted(items,key=lambda row:row['item_id']),'template_revision':revision,'schema_sha256':schema}


def outline(source_id,template_id,*,limit=20,offset=0,reader=None):
    from .native_once import selected_read_access
    from .native_read import NativeReader,native_config,validate_template_sections
    access=selected_read_access('reminders');config=native_config('reminders')
    identity(source_id);identity(template_id)
    if (source_id not in config.get('source_ids',[]) or config.get('metadata_backend')!='reminders-store-readonly'
            or config.get('advanced_backend')!='reminderkit' or not config.get('helper_path')):
        raise SafetyError('Template outlines require explicit Reminders store-readonly opt-in and the selected ReminderKit helper; no automatic fallback')
    if type(limit) is not int or not 1<=limit<=20 or type(offset) is not int or not 0<=offset<=100000:
        raise SafetyError('Template outline pages require limit 1..20 and offset 0..100000')
    if platform.system()!='Darwin' or platform.mac_ver()[0].split('.')[0]!='27':
        raise SafetyError('Optional Reminders template store reads are verified only for macOS 27')
    reader=reader or NativeReader('reminders')
    # This independent native read must pass OS authorization and the selected
    # account/template relationship before any local store is opened.
    before=reader.read('template_sections',source_id=source_id,id=template_id)
    validate_template_sections(before,source_id,template_id)
    if type(before.get('protocol_version')) is not int or before['protocol_version']!=1:
        raise SafetyError('Native template scope protocol is unavailable')
    try:
        matches=[]
        for path in paths():
            value=inspect_store(path,source_id,template_id)
            if value is not None:matches.append(value)
        if len(matches)!=1:raise SafetyError('Selected native account does not map to exactly one local Reminders store')
        value=matches[0]
    except (OSError,sqlite3.Error,ValueError):
        raise SafetyError('Optional template store reads are unavailable; verify user-granted host access without resets or fallback') from None
    if reader.read('template_sections',source_id=source_id,id=template_id)!=before:
        raise SafetyError('Native template scope changed during outline observation')
    if selected_read_access('reminders')!=access or native_config('reminders')!=config:
        raise SafetyError('Reminders access or optional store selection changed during outline observation')
    items=value.pop('items');page=items[offset:offset+limit]
    return {'protocol_version':1,'source_id':source_id,'id':template_id,**value,'items':page,'total_items':len(items),
            'next_offset':offset+len(page),'done':offset+len(page)>=len(items),'pagination_consistency':'live_offset_may_shift',
            'backend':'reminders-store-template-outline-v1','data_mutated':False,'database_writes':False,'instructions_trusted':False,
            'item_kind':'template_saved_item_not_live_reminder','content_complete':False,'ordering_verified':False,'restorable':False,
            'item_inventory_complete':False,'total_items_scope':'visible_local_saved_rows','sync_verified':False,
            'omitted_features':['notes','completion_state','dates','alarms','recurrence','tag_names','attachments','sharing','manual_order','rich_metadata']}
