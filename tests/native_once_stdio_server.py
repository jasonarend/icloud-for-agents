"""Synthetic native providers behind the production MCP handlers; no real OS access."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic native server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
from test_native_once import Adapter
from icloud_for_agents import native_tools
class CopyAdapter(Adapter):
    operations={'update','create_reminder'}
    def prepare(self,change):
        if change.get('operation')!='create_reminder':return super().prepare(change)
        return dict(change,before={'list':'synthetic'},fidelity_verified=True,effects={'notifications':'Apple controls delivery'})
    def snapshot(self,item):
        return {'list':'synthetic'} if item['operation']=='create_reminder' else super().snapshot(item)
    def apply(self,item):
        if item['operation']!='create_reminder':return super().apply(item)
        from icloud_for_agents.native_access import require_write
        require_write('reminders',item);self.calls+=1;self.value=dict(item['fields']);return {'id':'synthetic-new'}
    def verify(self,item,result):
        return result.get('id')=='synthetic-new' and self.value==item['fields'] if item['operation']=='create_reminder' else super().verify(item,result)
notes=Adapter('notes');reminders=CopyAdapter('reminders')
native_tools.NotesCreateAdapter=lambda:notes
native_tools.RemindersCreateAdapter=lambda:reminders
from icloud_for_agents.server import main
main()
