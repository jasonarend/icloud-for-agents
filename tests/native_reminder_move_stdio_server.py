"""Synthetic Reminder provider behind the real MCP and production move adapter."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic move server forbids real helpers/network')
sys.addaudithook(audit);atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from test_native_reminder_moves import Reader,ROOT,CHILD,DEST,ORIGIN
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_access import require_write
from icloud_for_agents import native_tools
class Adapter(RemindersCreateAdapter):
    @property
    def backend_id(self):return 'synthetic-subtree-mcp'
    def __init__(self):self.reader=Reader()
    def apply(self,item):
        require_write('reminders',item);request=mutation_request('reminders',item)['change']
        assert request['hierarchy_guard']=='verified_list_graphs' and len(request['before']['descendants'])==1
        destination=item['fields']['list_id']
        for identifier in (ROOT,CHILD):self.reader.items[identifier]['list_id']=destination;self.reader.items[identifier]['modified']+=1
        return {'id':ROOT,'source_id':item['source_id'],'list_id':destination,'moved_ids':sorted([ROOT,CHILD]),'saved_locally':True}
adapter=Adapter();native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
