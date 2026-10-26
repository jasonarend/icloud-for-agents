"""Client-neutral independent service selection with legacy DAV compatibility."""
import os
from .core import SafetyError


LEGACY_SERVICES = frozenset({'mail', 'calendar', 'contacts'})
NATIVE_SERVICES = frozenset({'notes', 'reminders'})
SERVICE_NAMES = LEGACY_SERVICES | NATIVE_SERVICES


def configured(settings):
    selection = settings.get('services')
    if selection is None:
        # Keep the legacy return shape: callers also use it for existing settings.
        return {'mail': True, 'calendar': settings['dav_reads'], 'contacts': settings['dav_reads']}
    if (not isinstance(selection, dict) or not LEGACY_SERVICES <= set(selection)
            or set(selection) - SERVICE_NAMES or any(type(v) is not bool for v in selection.values())):
        raise ValueError('Services must select mail, calendar and contacts with booleans; '
                         'notes and reminders are optional boolean selections')
    return dict(selection)


def enabled(name):
    if name not in SERVICE_NAMES:
        raise SafetyError('Unknown service')
    key = 'ICLOUD_ENABLE_' + name.upper()
    value = os.environ.get(key)
    if value is None:
        if name in NATIVE_SERVICES:
            value = '0'
        else:
            value = '1' if name == 'mail' else os.environ.get('ICLOUD_ENABLE_DAV_READ', '0')
    if value not in {'0', '1'}:
        raise SafetyError('Invalid service switch; use explicit 0 or 1')
    return value == '1'
