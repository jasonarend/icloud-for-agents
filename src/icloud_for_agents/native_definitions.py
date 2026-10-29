"""Bounded data-only JSON parsing for native definitions; never evaluates filters."""
import json
import math
from .core import SafetyError

MAX_BYTES = 65536


def parse(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    def constant(_):
        raise ValueError('Non-JSON number')

    try:
        if not isinstance(raw, str) or not 1 <= len(raw.encode('utf-8')) <= MAX_BYTES:
            raise ValueError('Definition size')
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError('Definition must be an object')
        pending = [(value, 0)]
        nodes = 0
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if nodes > 2000 or depth > 20:
                raise ValueError('Definition complexity')
            if isinstance(item, dict):
                pending.extend((entry, depth + 1) for pair in item.items() for entry in pair)
            elif isinstance(item, list):
                pending.extend((entry, depth + 1) for entry in item)
            elif isinstance(item, str):
                item.encode('utf-8', errors='strict')
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError('Nonfinite JSON number')
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise SafetyError('Native definition is malformed or exceeds the verified size/complexity bounds') from None
