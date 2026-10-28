"""MCP executor with synthetic helper responses; no Apple or network access."""
import json,os,sys
from pathlib import Path
from types import SimpleNamespace
sys.addaudithook(lambda event,args: (_ for _ in ()).throw(AssertionError('No network or real helper'))
                 if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'} else None)
from icloud_for_agents import native_tools
from icloud_for_agents.native_read import NativeReader
from icloud_for_agents.native_protocol import mutation_request

helper=Path(os.environ['HOME'])/'synthetic-helper';helper.write_text('synthetic');helper.chmod(0o700)
class Adapter:
    backend_id='synthetic-reminder-exchange-v1';operations={'update_reminder'}
    def prepare(self,change):
        before={'list':{'id':'list','source_id':'selected','writable':True},'item':{'id':'task','source_id':'selected','list_id':'list','notes':'private synthetic before'}}
        return {**change,'before':before,'fidelity_verified':True,'effects':{}}
    def snapshot(self,item):return item['before']
    def apply(self,item):return NativeReader('reminders')._dispatch(mutation_request('reminders',item),{'helper_path':str(helper)})['result']
    def verify(self,item,result):raise AssertionError('Synthetic helper intentionally reports uncertainty')
def synthetic_run(command,**kwargs):
    kwargs['stdout'].write(json.dumps({'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True}).encode())
    return SimpleNamespace(returncode=0)
native_tools.RemindersCreateAdapter=lambda:Adapter()
import icloud_for_agents.native_reminder_recovery as recovery
recovery.RemindersCreateAdapter=lambda:Adapter()
import icloud_for_agents.native_read as native_read
native_read.subprocess.run=synthetic_run
from icloud_for_agents.server import main
main()
