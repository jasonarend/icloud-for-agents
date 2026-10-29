"""Bounded data-only Smart Folder definitions; never a query evaluator or writer."""
import sqlite3

from .core import SafetyError
from .native_note_assets import _access, _current
from .native_note_store import folder_role, parse_identity, readonly_store

from .native_definitions import MAX_BYTES, parse as _json


def _definition(folder_id, source_ids):
    role = folder_role(folder_id, source_ids)
    if role['marked_for_deletion'] or not role['has_smart_query']:
        raise SafetyError('Exact live folder has no native Smart Folder definition')
    _, folder = parse_identity(folder_id, 'ICFolder')
    _, account = parse_identity(role['source_id'], 'ICAccount')
    with readonly_store() as (db, _):
        # Bound SQLite materialization as well as the subsequently parsed JSON.
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_BYTES + 4096)
        entities = db.execute("SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME='ICFolder' LIMIT 2").fetchall()
        if len(entities) != 1 or type(entities[0][0]) is not int:
            raise SafetyError('Native Smart Folder entity is unavailable')
        row = db.execute('SELECT Z_OPT,ZSMARTFOLDERQUERYJSON FROM ZICCLOUDSYNCINGOBJECT WHERE Z_PK=? AND Z_ENT=? AND ZACCOUNT8=? AND ZMARKEDFORDELETION=0',
                         (folder, entities[0][0], account)).fetchone()
        if row is None or row[0] != role['revision']:
            raise SafetyError('Native Smart Folder changed during observation')
        value = _json(row[1])
    if folder_role(folder_id, source_ids) != role:
        raise SafetyError('Native Smart Folder scope or metadata changed during observation')
    return role, value


def definition(folder_id):
    access = _access()
    before = _definition(folder_id, access[0]['source_ids'])
    if _definition(folder_id, access[0]['source_ids']) != before:
        raise SafetyError('Native Smart Folder definition changed during observation')
    _current(access)
    role, value = before
    return {'protocol_version': 1, 'folder_id': folder_id, 'source_id': role['source_id'],
            'revision': role['revision'], 'native_folder_type': role['native_folder_type'],
            'definition': value, 'definition_format': 'uninterpreted_native_JSON',
            'semantics_verified': False, 'membership_evaluated': False,
            'instructions_trusted': False, 'content_read': False, 'data_mutated': False,
            'backend': 'notes-store-smart-definition-v1',
            'consistency': 'separate_live_reads_may_change'}
