"""Optional exact-note metadata and generated PDF reads from the local Notes store.

No database writes, key reads, arbitrary paths or original-file fidelity claim.
"""
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from .core import SafetyError
from .native_note_store import readonly_store, note_location, parse_identity
from .native_read import native_config, NativeReader
from .native_once import selected_read_access

MAX_BYTES = 1048576
ASSET_ID = re.compile(r'x-coredata://([0-9A-Fa-f-]{36})/ICAttachment/p([1-9][0-9]{0,17})\Z')
GENERATION = re.compile(r'[1-9][0-9]{0,8}_[0-9A-Fa-f-]{36}\Z')
ASSET_COLUMNS = ('Z_PK', 'Z_ENT', 'Z_OPT', 'ZIDENTIFIER', 'ZNOTE', 'ZTYPEUTI',
                 'ZPARENTATTACHMENT', 'ZFALLBACKPDFGENERATION',
                 'ZFALLBACKPDFCRYPTOINITIALIZATIONVECTOR', 'ZFALLBACKPDFCRYPTOTAG')
CRYPTO_FLAGS = {'ZFALLBACKPDFCRYPTOINITIALIZATIONVECTOR', 'ZFALLBACKPDFCRYPTOTAG'}


def _uuid(value):
    try:
        if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value.lower():
            raise ValueError()
    except ValueError:
        raise SafetyError('Native asset storage identity is unavailable') from None
    return value


def _access():
    status = selected_read_access('notes')
    config = native_config('notes')
    if config.get('metadata_backend') != 'notes-store-readonly':
        raise SafetyError('Native asset metadata requires explicitly selected notes-store-readonly access')
    return config, status['effective_access']


def _current(access):
    if _access() != access:
        raise SafetyError('Notes access changed during the native metadata read')


def _state(folder_id, identity, *, assets=False):
    access = _access()
    store, note = parse_identity(identity, 'ICNote')
    folder_store, folder = parse_identity(folder_id, 'ICFolder')
    if store != folder_store:
        raise SafetyError('The exact note and folder must share one native store')
    location = note_location(identity, access[0]['source_ids'])
    if location['folder_id'] != folder_id:
        raise SafetyError('Native note is outside the exact selected physical folder')
    _, account = parse_identity(location['source_id'], 'ICAccount')
    with readonly_store() as (db, schema):
        columns = {row[1] for row in db.execute('PRAGMA table_info(ZICCLOUDSYNCINGOBJECT)')}
        required = {'ZFOLDER', 'ZISPINNED'} | (set(ASSET_COLUMNS) if assets else set())
        if not required <= columns:
            raise SafetyError('Native Notes schema lacks the verified metadata fields')
        entity_rows = db.execute("SELECT Z_NAME,Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME IN ('ICAccount','ICNote','ICAttachment')").fetchall()
        entities = dict(entity_rows)
        if (len(entities) != len(entity_rows) or not {'ICAccount', 'ICNote'} <= entities.keys()
                or any(type(value) is not int or value < 1 for value in entities.values())):
            raise SafetyError('Native Notes entities cannot be verified')
        note_row = db.execute('SELECT Z_ENT,Z_OPT,ZFOLDER,ZMARKEDFORDELETION,ZISPINNED FROM ZICCLOUDSYNCINGOBJECT WHERE Z_PK=?', (note,)).fetchone()
        account_row = db.execute('SELECT Z_ENT,ZIDENTIFIER FROM ZICCLOUDSYNCINGOBJECT WHERE Z_PK=?', (account,)).fetchone() if assets else None
        if (note_row is None or note_row[0] != entities.get('ICNote') or type(note_row[1]) is not int
                or note_row[2] != folder or note_row[3] != 0
                or (note_row[4] is not None and (type(note_row[4]) is not int or note_row[4] not in (0, 1)))):
            raise SafetyError('Exact live note metadata changed or is unavailable')
        result = {'id': identity, 'folder_id': folder_id, 'source_id': location['source_id'],
                  'note_revision': note_row[1], 'pinned': None if note_row[4] is None else bool(note_row[4]),
                  'schema_sha256': schema}
        if assets:
            if (account_row is None or account_row[0] != entities.get('ICAccount')
                    or type(entities.get('ICAttachment')) is not int):
                raise SafetyError('Selected native asset account/entity is unavailable')
            result['account_storage_id'] = _uuid(account_row[1])
            # Read only crypto nullness, never encryption bytes or keys.
            projection = ','.join(column + ' IS NOT NULL' if column in CRYPTO_FLAGS else column for column in ASSET_COLUMNS)
            rows = db.execute('SELECT ' + projection + ' FROM ZICCLOUDSYNCINGOBJECT WHERE ZNOTE=? AND Z_ENT=? AND ZMARKEDFORDELETION=0 LIMIT 101', (note, entities['ICAttachment'])).fetchall()
            if len(rows) > 100:
                raise SafetyError('Native asset observation exceeds the 100-object bound')
            values = []
            for row in rows:
                value = dict(zip(ASSET_COLUMNS, row))
                if (type(value['Z_PK']) is not int or value['Z_PK'] < 1 or type(value['Z_OPT']) is not int
                        or value['ZNOTE'] != note or value['Z_ENT'] != entities['ICAttachment']
                        or not isinstance(value['ZTYPEUTI'], str) or not 1 <= len(value['ZTYPEUTI']) <= 255
                        or (value['ZPARENTATTACHMENT'] is not None and (type(value['ZPARENTATTACHMENT']) is not int or value['ZPARENTATTACHMENT'] < 1))):
                    raise SafetyError('Incomplete native asset identity or note relation')
                _uuid(value['ZIDENTIFIER'])
                values.append(value)
            result['assets'] = sorted(values, key=lambda row: row['Z_PK'])
    _current(access)
    return result, access


def metadata(folder_id, identity):
    value, _ = _state(folder_id, identity)
    return {key: value[key] for key in ('id', 'folder_id', 'source_id', 'note_revision', 'pinned')} | {
        'protocol_version': 1, 'pinned_status': 'unavailable' if value['pinned'] is None else 'provider_reported',
        'backend': 'notes-store-note-metadata-v1', 'content_read': False, 'data_mutated': False,
        'consistency': 'separate_live_reads_may_change'}


def _generation(value):
    if not isinstance(value, str) or GENERATION.fullmatch(value) is None:
        raise SafetyError('The native PDF generation is unsupported or unavailable')
    _uuid(value.split('_', 1)[1])
    return value


def _unlocked(reader, folder_id, identity, source_ids):
    observed = reader.read('get', folder_id=folder_id, id=identity)
    public = observed.get('item', {})
    if (public.get('id') != identity or public.get('folder_id') != folder_id
            or public.get('source_id') not in source_ids or public.get('locked') is not False):
        raise SafetyError('Native assets require the exact unlocked note in the selected folder/account')
    return observed


def inventory(folder_id, identity, *, reader=None):
    access = _access()
    reader = reader or NativeReader('notes')
    native_before = _unlocked(reader, folder_id, identity, access[0]['source_ids'])
    value, _ = _state(folder_id, identity, assets=True)
    if native_before['item']['source_id'] != value['source_id']:
        raise SafetyError('Native asset and app account observations disagree')
    store = identity.split('/')[2]
    observed_ids = {row['Z_PK'] for row in value['assets']}
    rows = []
    for asset in value['assets']:
        try:
            _generation(asset['ZFALLBACKPDFGENERATION'])
            candidate = asset['ZTYPEUTI'] == 'com.apple.paper.doc.pdf'
        except SafetyError:
            candidate = False
        rows.append({'id': f'x-coredata://{store}/ICAttachment/p{asset["Z_PK"]}',
                     'native_type': asset['ZTYPEUTI'], 'revision': asset['Z_OPT'],
                     'parent_asset_id': f'x-coredata://{store}/ICAttachment/p{asset["ZPARENTATTACHMENT"]}' if asset['ZPARENTATTACHMENT'] in observed_ids and asset['ZPARENTATTACHMENT'] != asset['Z_PK'] else None,
                     'parent_status': 'not_reported' if asset['ZPARENTATTACHMENT'] is None else 'in_observed_note' if asset['ZPARENTATTACHMENT'] in observed_ids and asset['ZPARENTATTACHMENT'] != asset['Z_PK'] else 'outside_observed_scope',
                     'generated_pdf_candidate': candidate, 'file_availability': 'not_checked'})
    if _state(folder_id, identity, assets=True)[0] != value or reader.read('get', folder_id=folder_id, id=identity) != native_before:
        raise SafetyError('Note or native metadata changed during asset observation')
    _current(access)
    return {key: value[key] for key in ('id', 'folder_id', 'source_id', 'note_revision')} | {
        'protocol_version': 1, 'assets': rows, 'native_asset_inventory_complete': False,
        'coverage': 'direct_live_ICAttachment_rows_only; inline and descendant representations may be omitted',
        'backend': 'notes-store-assets-v1', 'asset_content_read': False,
        'note_content_read_for_guard': True, 'content_returned': False, 'data_mutated': False,
        'consistency': 'separate_live_reads_may_change'}


def group_root():
    return Path.home() / 'Library/Group Containers/group.com.apple.notes'


def _pdf_bytes(account, asset, generation, maximum):
    descriptors = []
    try:
        directory = os.open(group_root(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        descriptors.append(directory)
        for component in ('Accounts', _uuid(account), 'FallbackPDFs', _uuid(asset), _generation(generation)):
            info = os.fstat(directory)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise SafetyError('Native PDF directories must be owned and not writable by other users')
            directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            descriptors.append(directory)
        info = os.fstat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise SafetyError('Native PDF directory is unsafe')
        fd = os.open('FallbackPDF.pdf', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        descriptors.append(fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1
                or before.st_mode & 0o022 or not 5 <= before.st_size <= maximum):
            raise SafetyError('Native generated PDF is unavailable, unsafe or exceeds max_bytes')
        chunks = []
        remaining = maximum + 1
        while remaining:
            part = os.read(fd, min(65536, remaining))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        raw = b''.join(chunks)
        after = os.fstat(fd)
        signature = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)
        if signature(before) != signature(after) or len(raw) != before.st_size or not raw.startswith(b'%PDF-'):
            raise SafetyError('Native PDF changed during reading or is not a PDF representation')
        return raw
    except OSError:
        raise SafetyError('Native generated PDF is not locally available; no download or fallback was attempted') from None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def export_pdf(folder_id, identity, asset_id, maximum=MAX_BYTES, *, reader=None):
    if type(maximum) is not int or not 1 <= maximum <= MAX_BYTES:
        raise SafetyError('max_bytes must be from 1 to 1048576')
    match = ASSET_ID.fullmatch(asset_id) if isinstance(asset_id, str) else None
    if match is None:
        raise SafetyError('Use one exact native ICAttachment identity; no paths')
    _uuid(match[1])
    before, access = _state(folder_id, identity, assets=True)
    if match[1].lower() != parse_identity(identity, 'ICNote')[0]:
        raise SafetyError('Native asset belongs to a different store')
    assets = [row for row in before['assets'] if row['Z_PK'] == int(match[2])]
    if (len(assets) != 1 or assets[0]['ZTYPEUTI'] != 'com.apple.paper.doc.pdf'
            or assets[0]['ZFALLBACKPDFCRYPTOINITIALIZATIONVECTOR'] != 0
            or assets[0]['ZFALLBACKPDFCRYPTOTAG'] != 0):
        raise SafetyError('Exact unencrypted native paper-PDF asset is unavailable in this note')
    asset = assets[0]
    generation = _generation(asset['ZFALLBACKPDFGENERATION'])
    reader = reader or NativeReader('notes')
    native_before = _unlocked(reader, folder_id, identity, [before['source_id']])
    public = native_before['item']
    raw = _pdf_bytes(before['account_storage_id'], asset['ZIDENTIFIER'], generation, maximum)
    if _state(folder_id, identity, assets=True)[0] != before or reader.read('get', folder_id=folder_id, id=identity) != native_before:
        raise SafetyError('Note or native asset metadata changed during PDF export')
    _current(access)
    return {'protocol_version': 1, 'id': identity, 'folder_id': folder_id, 'source_id': before['source_id'],
            'asset_id': asset_id, 'modified': public.get('modified'), 'encoding': 'base64',
            'data': base64.b64encode(raw).decode('ascii'), 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'representation': 'Notes_generated_fallback_pdf', 'original_file': False, 'complete_backup': False,
            'losses': ['Notes generated this PDF; original file bytes, editable objects and complete native fidelity are not preserved.'],
            'download_attempted': False, 'data_mutated': False}
