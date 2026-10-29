"""Exact-note native inline tag observations, without interpreting plain hashtags."""
import sqlite3

from .core import SafetyError
from .native_note_assets import _access, _current, _state, _unlocked, _uuid
from .native_note_store import parse_identity, readonly_store
from .native_read import NativeReader

TAG_UTI = 'com.apple.notes.inlinetextattachment.hashtag'


def _tags(folder_id, identity):
    note, access = _state(folder_id, identity)
    store, pk = parse_identity(identity, 'ICNote')
    _, account = parse_identity(note['source_id'], 'ICAccount')
    with readonly_store() as (db, _):
        required = {'ZNOTE1', 'ZACCOUNT4', 'ZTYPEUTI1', 'ZALTTEXT', 'ZTOKENCONTENTIDENTIFIER', 'ZIDENTIFIER'}
        columns = {row[1] for row in db.execute('PRAGMA table_info(ZICCLOUDSYNCINGOBJECT)')}
        if not required <= columns:
            raise SafetyError('Native Notes schema lacks the verified inline tag relation')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 131072)
        entities = db.execute("SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME='ICInlineAttachment' LIMIT 2").fetchall()
        if len(entities) != 1 or type(entities[0][0]) is not int or entities[0][0] < 1:
            raise SafetyError('Native inline tag entity is unavailable')
        rows = db.execute('SELECT Z_PK,Z_OPT,ZNOTE1,ZACCOUNT4,ZIDENTIFIER,ZALTTEXT,ZTOKENCONTENTIDENTIFIER FROM ZICCLOUDSYNCINGOBJECT WHERE ZNOTE1=? AND Z_ENT=? AND ZTYPEUTI1=? AND ZMARKEDFORDELETION=0 LIMIT 101',
                          (pk, entities[0][0], TAG_UTI)).fetchall()
        if len(rows) > 100:
            raise SafetyError('Native tag observation exceeds the 100-occurrence bound')
        tags = []
        for native_pk, revision, parent, owner, storage_id, display, token in rows:
            if (type(native_pk) is not int or native_pk < 1 or type(revision) is not int
                    or parent != pk or owner != account
                    or not isinstance(display, str) or not display.startswith('#')
                    or not isinstance(token, str) or not 1 <= len(token) <= 1000
                    or not 2 <= len(display) <= 1001):
                raise SafetyError('Native inline tag identity, account or display value is unavailable')
            _uuid(storage_id)
            try:
                display.encode('utf-8', errors='strict');token.encode('utf-8', errors='strict')
            except UnicodeError:
                raise SafetyError('Native inline tag contains invalid Unicode') from None
            tags.append({'id': f'x-coredata://{store}/ICInlineAttachment/p{native_pk}',
                         'revision': revision, 'display_text': display, 'native_token': token})
    if _state(folder_id, identity)[0] != note:
        raise SafetyError('Native note changed during inline tag observation')
    _current(access)
    return note, sorted(tags, key=lambda row: row['id'])


def inventory(folder_id, identity, *, reader=None):
    access = _access()
    reader = reader or NativeReader('notes')
    before = _unlocked(reader, folder_id, identity, access[0]['source_ids'])
    note, tags = _tags(folder_id, identity)
    if note['source_id'] != before['item']['source_id']:
        raise SafetyError('Native tag and app account observations disagree')
    if _tags(folder_id, identity) != (note, tags) or reader.read('get', folder_id=folder_id, id=identity) != before:
        raise SafetyError('Native tags or note changed during observation')
    _current(access)
    return {'protocol_version': 1, 'id': identity, 'folder_id': folder_id, 'source_id': note['source_id'],
            'note_revision': note['note_revision'], 'tags': tags,
            'native_tag_inventory_complete': False,
            'coverage': 'live direct ICInlineAttachment hashtag rows only; plain text is not treated as native tags',
            'note_content_read_for_guard': True, 'content_returned': False, 'data_mutated': False,
            'backend': 'notes-store-inline-tags-v1', 'consistency': 'separate_live_reads_may_change'}
