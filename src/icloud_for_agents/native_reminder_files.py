"""Bounded exact ReminderKit local file reads; never fetch URLs or caller paths."""
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from .core import SafetyError
from .native_once import selected_read_access
from .native_read import NativeReader, native_config

MAX_BYTES = 1048576
FILE_LEAF = re.compile(r'[0-9a-f]{128}\.[a-z0-9]{1,16}\Z')


def validate_id(value):
    try:
        if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value.lower():
            raise ValueError()
    except ValueError:
        raise SafetyError('Use one exact native Reminder or attachment UUID; no paths') from None
    return str(uuid.UUID(value)).upper()


def validate_descriptor(value, fields, sources):
    keys = {'protocol_version', 'id', 'source_id', 'list_id', 'attachment_id', 'kind', 'uti', 'file_leaf', 'file_size', 'data_mutated', 'download_requested'}
    if (not isinstance(value, dict) or set(value) != keys or type(value.get('protocol_version')) is not int
            or value['protocol_version'] != 1 or value.get('id') != fields['id']
            or value.get('attachment_id') != fields['attachment_id'] or value.get('source_id') not in sources
            or value.get('kind') not in {'image', 'file'} or not isinstance(value.get('uti'), str)
            or not 1 <= len(value['uti']) <= 512 or any(0xD800 <= ord(c) <= 0xDFFF for c in value['uti'])
            or len(value['uti'].encode('utf-8', 'surrogatepass')) > 512 or not isinstance(value.get('file_leaf'), str)
            or FILE_LEAF.fullmatch(value['file_leaf']) is None or type(value.get('file_size')) is not int
            or not 0 <= value['file_size'] <= MAX_BYTES or value.get('data_mutated') is not False
            or value.get('download_requested') is not False):
        raise SafetyError('Exact bounded native Reminder attachment file did not verify')
    validate_id(value['source_id']);validate_id(value['list_id'])
    return value


def _access():
    status = selected_read_access('reminders')
    config = native_config('reminders')
    if config.get('advanced_backend') != 'reminderkit' or not config.get('helper_path'):
        raise SafetyError('Reminder file export requires explicitly selected ReminderKit and the native helper')
    return config, status['effective_access']


def group_root():
    return Path.home() / 'Library/Group Containers/group.com.apple.reminders'


def _bytes(source, leaf, size, maximum):
    if not isinstance(leaf, str) or FILE_LEAF.fullmatch(leaf) is None or type(size) is not int or not 0 <= size <= maximum:
        raise SafetyError('Native attachment file exceeds max_bytes or has an unsupported storage identity')
    descriptors = []
    try:
        directory = os.open(group_root(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        descriptors.append(directory)
        for component in ('Container_v1', 'Files', 'Account-' + validate_id(source), 'Attachments'):
            info = os.fstat(directory)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise SafetyError('Reminder file directories must be owned and not writable by other users')
            directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            descriptors.append(directory)
        info = os.fstat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise SafetyError('Reminder attachment directory is unsafe')
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_mode & 0o022 or before.st_size != size:
            raise SafetyError('Reminder attachment is not the verified owned regular file')
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk);remaining -= len(chunk)
        raw = b''.join(chunks)
        after = os.fstat(fd)
        signature = lambda v: (v.st_dev, v.st_ino, v.st_size, v.st_mtime_ns, v.st_ctime_ns, v.st_nlink)
        if (signature(before) != signature(after) or len(raw) != size
                or hashlib.sha512(raw).hexdigest() != leaf.split('.', 1)[0]):
            raise SafetyError('Reminder attachment changed or its storage digest did not verify')
        return raw
    except OSError:
        raise SafetyError('Native Reminder attachment is not safely available locally; no download attempted') from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def export(identity, attachment_id, maximum=MAX_BYTES, *, reader=None):
    validate_id(identity);validate_id(attachment_id)
    if type(maximum) is not int or not 1 <= maximum <= MAX_BYTES:
        raise SafetyError('max_bytes must be from 1 to 1048576')
    access = _access()
    reader = reader or NativeReader('reminders')
    before = reader.read('get', id=identity, include_native_attachments=True)
    item = before.get('item', {})
    if item.get('id') != identity or item.get('source_id') not in access[0]['source_ids']:
        raise SafetyError('Exact Reminder is outside the selected source')
    fields = {'id': identity, 'attachment_id': attachment_id}
    value = validate_descriptor(reader.read('attachment_file', **fields), fields, access[0]['source_ids'])
    attachments = item.get('native_metadata', {}).get('attachments', [])
    observed = [a for a in attachments if a.get('id') == attachment_id]
    if (value['source_id'] != item['source_id'] or value['list_id'] != item.get('list_id') or len(observed) != 1
            or any(observed[0].get(key) != value[key] for key in ('kind', 'uti'))):
        raise SafetyError('Native attachment file and Reminder observations disagree')
    raw = _bytes(value['source_id'], value['file_leaf'], value['file_size'], maximum)
    if reader.read('attachment_file', **fields) != value or reader.read('get', id=identity, include_native_attachments=True) != before or _access() != access:
        raise SafetyError('Reminder, attachment file or access changed during export')
    return {'protocol_version': 1, 'id': identity, 'attachment_id': attachment_id,
            'source_id': value['source_id'], 'list_id': value['list_id'], 'kind': value['kind'], 'uti': value['uti'],
            'encoding': 'base64', 'data': base64.b64encode(raw).decode('ascii'), 'size': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(), 'representation': 'ReminderKit_local_file',
            'original_file': False, 'complete_backup': False,
            'losses': ['Reminders may convert imported files; the stored representation does not establish original file bytes.'],
            'download_attempted': False, 'data_mutated': False}
