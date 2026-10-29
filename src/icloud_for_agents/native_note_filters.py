"""Bounded physical-folder queries over observed native pin and tag metadata."""
from .core import SafetyError
from .native_note_assets import _access, _current, metadata
from .native_note_store import parse_identity
from .native_note_tags import inventory
from .native_read import NativeReader


def query(folder_id, *, pinned=None, tag_token=None, limit=5, offset=0, reader=None):
    if pinned is not None and type(pinned) is not bool:
        raise SafetyError('Pinned must be an exact boolean or omitted')
    if tag_token is not None:
        if (not isinstance(tag_token, str) or not 1 <= len(tag_token) <= 1000
                or any(0xD800 <= ord(c) <= 0xDFFF for c in tag_token)):
            raise SafetyError('Use the exact bounded native_token returned by notes_native_tags')
    if pinned is None and tag_token is None:
        raise SafetyError('Select pinned or an exact native tag token; use notes_query for ordinary titles')
    if type(limit) is not int or not 1 <= limit <= 10 or type(offset) is not int or not 0 <= offset <= 100000:
        raise SafetyError('Native metadata queries scan 1..10 notes at offset 0..100000')
    parse_identity(folder_id, 'ICFolder')
    access = _access()
    reader = reader or NativeReader('notes')
    arguments = {'text': '', 'folder_id': folder_id, 'limit': limit, 'offset': offset, 'match_scope': 'title'}
    page = reader.read('search', **arguments)
    rows = page.get('items')
    if (not isinstance(rows, list) or len(rows) > limit
            or type(page.get('done')) is not bool or type(page.get('next_offset')) is not int
            or not offset <= page['next_offset'] <= offset + limit
            or type(page.get('scanned')) is not int or page['scanned'] != page['next_offset'] - offset
            or (not page['done'] and page['next_offset'] == offset)
            or any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                   or row.get('folder_id') != folder_id or row.get('source_id') not in access[0]['source_ids']
                   or not isinstance(row.get('title'), str) or len(row['title']) > 1000
                   or type(row.get('locked')) is not bool for row in rows)):
        raise SafetyError('Native metadata query requires an exact bounded physical-folder title page')
    if len({row['id'] for row in rows}) != len(rows):
        raise SafetyError('Native metadata query returned duplicate note identities')
    items, unavailable, observations = [], list(page.get('unavailable', [])), []
    for row in rows:
        identity = row['id']
        result = {key: row[key] for key in ('id', 'folder_id', 'source_id', 'title', 'locked')}
        try:
            if pinned is not None:
                pin = metadata(folder_id, identity)
                if (pin.get('id') != identity or pin.get('folder_id') != folder_id
                        or pin.get('source_id') != row['source_id']):
                    raise SafetyError('Pin metadata identity differs from the selected title page')
                observations.append(('pin', identity, pin))
                if type(pin.get('pinned')) is not bool:
                    unavailable.append({'id': identity, 'category': 'native_pin_unavailable'})
                    continue
                if pin['pinned'] is not pinned:
                    continue
                result['pinned'] = pin['pinned']
            if tag_token is not None:
                if row['locked']:
                    unavailable.append({'id': identity, 'category': 'locked_note_native_tags_unavailable'})
                    continue
                tags = inventory(folder_id, identity, reader=reader)
                if (tags.get('id') != identity or tags.get('folder_id') != folder_id
                        or tags.get('source_id') != row['source_id']):
                    raise SafetyError('Native tag identity differs from the selected title page')
                observations.append(('tag', identity, tags))
                matches = [tag for tag in tags['tags'] if tag['native_token'] == tag_token]
                if not matches:
                    continue
                result['matched_native_tag_count'] = len(matches)
            items.append(result)
        except SafetyError:
            # An unreadable note is not evidence that its native metadata is absent.
            unavailable.append({'id': identity, 'category': 'native_metadata_unavailable'})
        _current(access)
    if reader.read('search', **arguments) != page:
        raise SafetyError('Physical-folder title page changed during native metadata query')
    for kind, identity, value in observations:
        current = metadata(folder_id, identity) if kind == 'pin' else inventory(folder_id, identity, reader=reader)
        if current != value:
            raise SafetyError('Native pin or tag observations changed during query')
    _current(access)
    return {'protocol_version': 1, 'folder_id': folder_id, 'items': items, 'unavailable': unavailable,
            'done': page['done'], 'next_offset': page['next_offset'], 'scanned': page['scanned'],
            'filters': {'pinned': pinned, 'tag_token': tag_token, 'combination': 'all_requested'},
            'tag_matching': 'exact native_token; positive observed direct inline occurrences only',
            'native_tag_inventory_complete': False, 'smart_folder_membership_evaluated': False,
            'note_content_read_for_guard': tag_token is not None, 'content_returned': False,
            'data_mutated': False, 'backend': 'notes-store-filter-page-v1',
            'consistency': 'separate_live_reads_may_change; offsets are not stable cursors'}
