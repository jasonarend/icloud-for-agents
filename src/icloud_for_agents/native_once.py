"""Exact-batch temporary authority; never changes settings or process environment.

Planning admits reads only. Dispatch authority exists only around one consumed
plan item's adapter call, binds its full prepared payload, and is reset on every
exit. The ordinary plan executor retains strict/trusted approval and replay rules.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from .core import SafetyError, digest

_planning=ContextVar('native_once_planning',default=None)
_dispatch=ContextVar('native_once_dispatch',default=None)


def selected_read_access(name):
    from .native_access import access_status
    status=access_status(name)
    if status['effective_access']=='disabled' or not status['scope_selected']:
        raise SafetyError('Temporary native authority requires an enabled service and selected sources')
    return status


@contextmanager
def planning(name):
    selected_read_access(name)
    authority={'service':name,'active':True};token=_planning.set(authority)
    try:yield
    finally:
        authority['active']=False
        _planning.reset(token)


def planning_access(name):
    authority=_planning.get()
    if authority and authority['active'] and authority['service']==name:return selected_read_access(name)
    from .native_access import require_write
    return require_write(name)


@contextmanager
def dispatch(name,item):
    status=selected_read_access(name)
    if item.get('source_id') not in status['source_ids']:raise SafetyError('Temporary target source was revoked')
    authority={'service':name,'item_sha256':digest(item),'active':True};token=_dispatch.set(authority)
    try:yield
    finally:
        authority['active']=False
        _dispatch.reset(token)


def permits(name,item):
    authority=_dispatch.get()
    return bool(authority and authority['active'] and isinstance(item,dict)
                and authority['service']==name and authority['item_sha256']==digest(item))
