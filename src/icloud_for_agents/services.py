"""Client-neutral service selection with legacy DAV compatibility."""
import os
from .core import SafetyError


def configured(settings):
    selection = settings.get('services')
    if selection is None:
        return {'mail': True, 'calendar': settings['dav_reads'], 'contacts': settings['dav_reads']}
    if not isinstance(selection, dict) or set(selection) != {'mail', 'calendar', 'contacts'} or any(type(v) is not bool for v in selection.values()):
        raise ValueError('Services must explicitly select mail, calendar and contacts with booleans')
    return dict(selection)


def enabled(name):
    if name not in {'mail', 'calendar', 'contacts'}:
        raise SafetyError('Unknown service')
    key = 'ICLOUD_ENABLE_' + name.upper()
    value = os.environ.get(key)
    if value is None:
        value = '1' if name == 'mail' else os.environ.get('ICLOUD_ENABLE_DAV_READ', '0')
    if value not in {'0', '1'}:
        raise SafetyError('Invalid service switch; use explicit 0 or 1')
    return value == '1'
