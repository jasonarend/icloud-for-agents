"""Production alarm plans and MCP approvals with an in-memory provider."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic alarm server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_access import require_write
from test_native_reminders import Reader
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-native-alarm'
    def __init__(self):
        self.reader=Reader();self.reader.item.update(public_metadata_complete=True,has_recurrence=False,recurrence_rules=[],has_alarms=False,alarms=[],created=12345,url=None,location=None)
    def apply(self,item):
        require_write('reminders',item);assert item['operation']=='update_reminder';out=[]
        for alarm in item['fields']['alarms']:
            geo=alarm['location'];place={k:v for k,v in geo.items() if k!='proximity'}
            out.append({'type':0,'proximity':1 if geo['proximity']=='enter' else 2,'relative_offset_seconds':0,'absolute_date':None,'structured_location':place,'email_address':None,'sound_name':None})
        self.reader.item.update(alarms=out,has_alarms=bool(out))
        return {'id':item['id'],'source_id':'selected','list_id':'list'}
adapter=Adapter();native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
