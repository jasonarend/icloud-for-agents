"""Fixed, opted-in read-only section membership guard; never write SQLite."""
import json
import math
import platform
import sqlite3
import time
from .core import SafetyError
from .native_reminder_template_store import paths,identity,integer,_readonly_store

REQUIRED={
    'ZREMCDOBJECT':{'Z_PK','Z_ENT','ZIDENTIFIER','ZINACTIVE','ZMARKEDFORDELETION'},
    'ZREMCDBASELIST':{'Z_ENT','Z_OPT','ZIDENTIFIER','ZACCOUNT','ZMARKEDFORDELETION','ZMEMBERSHIPSOFREMINDERSINSECTIONSASDATA'},
    'Z_PRIMARYKEY':{'Z_NAME','Z_ENT'}}
APPLE_EPOCH=978307200


def memberships(raw):
    if raw is None:return {'minimumSupportedVersion':20230430,'memberships':[]}
    if not isinstance(raw,bytes) or not 1<=len(raw)<=65536:raise SafetyError('Unknown or oversized section membership representation')
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:raise ValueError('duplicate key')
            result[key]=value
        return result
    try:value=json.loads(raw,object_pairs_hook=pairs)
    except (ValueError,UnicodeError,RecursionError):raise SafetyError('Unknown section membership JSON') from None
    if (not isinstance(value,dict) or set(value)!={'memberships','minimumSupportedVersion'}
            or type(value['minimumSupportedVersion']) is not int or value['minimumSupportedVersion']!=20230430
            or not isinstance(value['memberships'],list) or len(value['memberships'])>200):
        raise SafetyError('Unsupported section membership version or bound')
    seen=set()
    for row in value['memberships']:
        if not isinstance(row,dict) or set(row) not in ({'memberID','modifiedOn'},{'memberID','groupID','modifiedOn'}):
            raise SafetyError('Unknown section membership fields')
        identity(row['memberID'])
        if 'groupID' in row:identity(row['groupID'])
        date=row['modifiedOn']
        if row['memberID'] in seen or type(date) not in (int,float) or not math.isfinite(date) or not 0<=date<time.time()-APPLE_EPOCH:
            raise SafetyError('Duplicate, invalid or future section membership timestamp')
        seen.add(row['memberID'])
    return dict(value,memberships=sorted(value['memberships'],key=lambda row:row['memberID']))


def inspect_store(path,source,list_id):
    with _readonly_store(path,REQUIRED) as (db,schema):
        records=db.execute("SELECT Z_NAME,Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME IN ('REMCDAccount','REMCDList')").fetchall();entities=dict(records)
        if len(records)!=2 or len(entities)!=2 or len(set(entities.values()))!=2 or any(not integer(v,1) for v in entities.values()):raise SafetyError('Unknown section entity mapping')
        accounts=db.execute('SELECT Z_PK,ZINACTIVE,ZMARKEDFORDELETION FROM ZREMCDOBJECT WHERE ZIDENTIFIER=? AND Z_ENT=? LIMIT 2',(identity(source),entities['REMCDAccount'])).fetchall()
        if not accounts:return None
        if len(accounts)!=1 or not integer(accounts[0][0],1) or accounts[0][1:]!=(0,0):raise SafetyError('Selected section account is ambiguous, inactive or deleted')
        rows=db.execute('SELECT Z_ENT,Z_OPT,ZACCOUNT,ZMARKEDFORDELETION,ZMEMBERSHIPSOFREMINDERSINSECTIONSASDATA FROM ZREMCDBASELIST WHERE ZIDENTIFIER=? LIMIT 2',(identity(list_id),)).fetchall()
        if len(rows)!=1 or rows[0][0]!=entities['REMCDList'] or not integer(rows[0][1]) or rows[0][2:4]!=(accounts[0][0],0):raise SafetyError('Section list is absent, deleted, ambiguous or outside its exact source')
        return {'schema_sha256':schema,'list_revision':rows[0][1],'value':memberships(rows[0][4])}


def read(source,list_id):
    from .native_once import selected_read_access
    from .native_read import native_config
    access=selected_read_access('reminders');config=native_config('reminders');identity(source);identity(list_id)
    if (source not in config.get('source_ids',[]) or config.get('metadata_backend')!='reminders-store-readonly'
            or config.get('advanced_backend')!='reminderkit' or not config.get('helper_path')):
        raise SafetyError('Section changes require explicit Reminders store-readonly opt-in and the selected ReminderKit helper')
    if platform.system()!='Darwin' or platform.mac_ver()[0].split('.')[0]!='27':raise SafetyError('Section store guards are verified only for macOS 27')
    # Caller has already bound native list scope and OS authorization. This
    # internal function has no MCP entry point and accepts no path, SQL or schema.
    try:matches=[value for path in paths() if (value:=inspect_store(path,source,list_id)) is not None]
    except (OSError,sqlite3.Error,ValueError):raise SafetyError('Optional section store guard is unavailable; no fallback or permission reset') from None
    if len(matches)!=1:raise SafetyError('Selected account does not map to exactly one Reminders store')
    if selected_read_access('reminders')!=access or native_config('reminders')!=config:raise SafetyError('Section metadata access changed during observation')
    return matches[0]
