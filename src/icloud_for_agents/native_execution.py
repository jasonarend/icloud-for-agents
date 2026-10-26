"""Ephemeral native execution provenance; this does not grant write authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from .core import digest
_current=ContextVar('native_execution_provenance',default=None)

@contextmanager
def capture(store,service,plan,index):
    value={'store':store,'service':service,'plan_id':plan['plan_id'],'plan_sha256':digest(plan),'index':index,'active':True}
    token=_current.set(value)
    try:yield
    finally:
        value['active']=False;_current.reset(token)

def current(service):
    value=_current.get()
    return value if value and value['active'] and value['service']==service else None
