"""Exact custom Smart List JSON observations without interpreting or executing them."""
from .core import SafetyError
from .native_definitions import parse
from .native_once import selected_read_access
from .native_read import NativeReader, native_config
from .native_reminder_files import validate_id


def definition(source_id, identity, *, reader=None):
    validate_id(source_id);validate_id(identity)
    access = selected_read_access('reminders')
    config = native_config('reminders')
    if (source_id not in config.get('source_ids', []) or config.get('advanced_backend') != 'reminderkit'
            or not config.get('helper_path')):
        raise SafetyError('Custom Smart List definitions require the exact selected source and optional ReminderKit helper')
    reader = reader or NativeReader('reminders')
    before = reader.read('smart_list_definition', source_id=source_id, id=identity)
    if (not isinstance(before, dict) or set(before) != {'protocol_version', 'source_id', 'id', 'title', 'definition_json', 'data_mutated', 'backend'}
            or type(before['protocol_version']) is not int or before['protocol_version'] != 1
            or before['source_id'] != source_id or before['id'] != identity
            or not isinstance(before['title'], str) or len(before['title']) > 1000
            or any(0xD800 <= ord(c) <= 0xDFFF for c in before['title'])
            or before['data_mutated'] is not False or before['backend'] != 'reminderkit-smart-definition-v1'):
        raise SafetyError('Helper did not verify the exact custom Smart List definition')
    value = parse(before['definition_json'])
    if reader.read('smart_list_definition', source_id=source_id, id=identity) != before:
        raise SafetyError('Custom Smart List definition changed during observation')
    if native_config('reminders') != config or selected_read_access('reminders') != access:
        raise SafetyError('Reminders access changed during Smart List observation')
    return {key: before[key] for key in ('protocol_version', 'source_id', 'id', 'title', 'backend', 'data_mutated')} | {
        'definition': value, 'definition_format': 'uninterpreted_native_JSON',
        'semantics_verified': False, 'membership_evaluated': False, 'instructions_trusted': False,
        'consistency': 'separate_live_reads_may_change'}
