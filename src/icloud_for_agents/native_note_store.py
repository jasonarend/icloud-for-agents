"""Explicitly opted-in, fixed-query Notes folder metadata. Never a database writer.

This optional macOS 27 route reads no note bodies, keys, blobs or attachments.
It binds native Core Data IDs to one store, entity and selected account relation.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import re
import sqlite3
import stat
from .core import SafetyError,digest

REQUIRED={'ZICCLOUDSYNCINGOBJECT':{'Z_PK','Z_ENT','Z_OPT','ZACCOUNT8','ZPARENT','ZFOLDERTYPE','ZMARKEDFORDELETION','ZSMARTFOLDERQUERYJSON','ZTITLE2'},
          'Z_PRIMARYKEY':{'Z_ENT','Z_NAME'},'Z_METADATA':{'Z_UUID'}}
IDENTITY=re.compile(r'x-coredata://([0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12})/(ICAccount|ICFolder)/p([1-9][0-9]{0,17})\Z')

def parse_identity(value,entity):
    match=IDENTITY.fullmatch(value) if isinstance(value,str) else None
    if not match or match[2]!=entity:raise SafetyError('Use an exact saved native Notes account/folder ID')
    return match[1].lower(),int(match[3])


def store_path():return Path.home()/'Library/Group Containers/group.com.apple.notes/NoteStore.sqlite'


@contextmanager
def readonly_store():
    if platform.system()!='Darwin' or platform.mac_ver()[0].split('.')[0]!='27':
        raise SafetyError('Optional Notes folder metadata is verified only for macOS 27; no fallback or inferred roles')
    path=store_path()
    try:
        for candidate in (path,Path(str(path)+'-wal'),Path(str(path)+'-shm')):
            try:info=candidate.lstat()
            except FileNotFoundError:
                if candidate==path:raise
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022:
                raise SafetyError('Notes store files must be regular, owned and not writable by other users')
        before=path.stat()
        connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3,isolation_level=None)
        try:
            connection.execute('PRAGMA query_only=ON');connection.execute('PRAGMA trusted_schema=OFF')
            def authorize(action,first,second,database,trigger):
                allowed=action in {sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION}
                if action==sqlite3.SQLITE_READ:allowed=first in {*REQUIRED,'sqlite_master','sqlite_schema'}
                if action==sqlite3.SQLITE_PRAGMA:allowed=first=='table_info' and second in REQUIRED
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
            connection.set_authorizer(authorize)
            remaining=[1000]
            def progress():remaining[0]-=1;return remaining[0]<=0
            connection.set_progress_handler(progress,1000)
            connection.execute('BEGIN')
            schema={}
            for table,columns in REQUIRED.items():
                kind=connection.execute('SELECT type FROM sqlite_master WHERE name=?',(table,)).fetchone()
                if kind!=('table',):raise SafetyError('Unknown Notes storage schema; no inferred role')
                # Table names come only from this fixed module, never caller data.
                schema[table]=[r[1:3] for r in connection.execute('PRAGMA table_info('+table+')')]
                if not columns<={r[0] for r in schema[table]}:raise SafetyError('Notes storage schema lacks verified role/account columns')
            after=path.stat()
            if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):raise SafetyError('Notes store changed during metadata admission')
            yield connection,digest(schema)
        finally:connection.close()
    except (OSError,sqlite3.Error):
        raise SafetyError('Optional Notes metadata unavailable. Verify this MCP host has user-granted macOS data access; no fallback, permission reset or empty result.') from None


def folder_role(folder_id,source_ids):
    store,folder=parse_identity(folder_id,'ICFolder')
    selected={}
    if not source_ids:raise SafetyError('Select exact Notes sources before metadata reads')
    for source in source_ids:
        source_store,account=parse_identity(source,'ICAccount')
        if source_store!=store:raise SafetyError('Selected Notes account is outside the exact native store')
        if account in selected:raise SafetyError('Ambiguous selected Notes account identity')
        selected[account]=source
    with readonly_store() as (connection,schema):
        uuids=connection.execute('SELECT Z_UUID FROM Z_METADATA LIMIT 2').fetchall()
        if len(uuids)!=1 or not isinstance(uuids[0][0],str) or uuids[0][0].lower()!=store:raise SafetyError('Native IDs do not match this Notes store')
        records=connection.execute("SELECT Z_NAME,Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME IN ('ICAccount','ICFolder')").fetchall();entities=dict(records)
        if len(records)!=2 or set(entities)!={'ICAccount','ICFolder'} or any(type(v) is not int for v in entities.values()):raise SafetyError('Native Notes entities cannot be verified')
        for account in selected:
            if connection.execute('SELECT Z_ENT FROM ZICCLOUDSYNCINGOBJECT WHERE Z_PK=?',(account,)).fetchone()!=(entities['ICAccount'],):raise SafetyError('Selected native account is unavailable or has a different entity')
        marks=','.join('?' for _ in selected)
        row=connection.execute('SELECT Z_ENT,Z_OPT,ZACCOUNT8,ZPARENT,ZFOLDERTYPE,ZMARKEDFORDELETION,ZSMARTFOLDERQUERYJSON IS NOT NULL,ZTITLE2 FROM ZICCLOUDSYNCINGOBJECT WHERE Z_PK=? AND ZACCOUNT8 IN ('+marks+')',(folder,*selected)).fetchone()
        if row is None or row[0]!=entities['ICFolder']:raise SafetyError('Folder is absent, outside selected accounts or not a native folder')
        entity,revision,account,parent,kind,deleted,smart,title=row
        if type(revision) is not int or type(kind) is not int or deleted not in (0,1) or not isinstance(title,str) or len(title)>1000:
            raise SafetyError('Incomplete native folder metadata')
        storage_kind='regular' if kind==0 and deleted==0 and smart==0 else 'special_smart_or_deleted'
        return {'protocol_version':1,'folder_id':folder_id,'source_id':selected[account],'title':title,
                'storage_kind':storage_kind,'native_folder_type':kind,'marked_for_deletion':bool(deleted),'has_smart_query':bool(smart),
                'revision':revision,'parent_pk':parent,'backend':'notes-store-role-v1','schema_sha256':schema,
                'default_folder_status':'requires independent Notes scripting check','content_read':False,'database_writes':False}
