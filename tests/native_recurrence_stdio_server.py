"""Actual native rule adapter and MCP permissions with in-memory provider state."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic rule server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_access import require_write
from test_native_reminders import Reader
from test_native_recurrence import native
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-native-rule'
    def __init__(self):
        self.reader=Reader();self.reader.item.update(public_metadata_complete=True,has_recurrence=False,recurrence_rules=[],created=12345,due={'year':2027,'month':1,'day':4,'date_only':True})
    def apply(self,item):
        require_write('reminders',item);assert item['operation']=='set_recurrence'
        rule=item['fields']['recurrence'];self.reader.item.update(recurrence_rules=[native(rule)] if rule else [],has_recurrence=bool(rule))
        return {'id':item['id'],'source_id':'selected','list_id':'list'}
adapter=Adapter();native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
