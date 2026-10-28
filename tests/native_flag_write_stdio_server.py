"""Production flag plans and MCP approvals with an in-memory provider."""
import atexit,copy,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic flag server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_access import require_write
from test_native_reminders import Reader
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-native-flag'
    def __init__(self):
        self.reader=Reader();self.reader.item.update(public_metadata_complete=True,has_recurrence=False,recurrence_rules=[],has_alarms=False,alarms=[],created=12345,url=None,location=None)
        self.flag=False;read=self.reader.read
        def enriched(action,**fields):
            result=read(action,**fields)
            if action=='get' and fields.get('include_native_urgent'):
                result['item']['native_metadata']={**{k:result['item'][k] for k in ('id','source_id','list_id')},'coverage':['urgent'],'urgent':False}
            if action=='get' and fields.get('include_native_metadata'):
                result['item']['native_metadata']={**{k:result['item'][k] for k in ('id','source_id','list_id')},'flagged':self.flag,'flag_state':int(self.flag),'tags':[],'tags_complete':True,'coverage':['flagged','tags','hierarchy','children','section'],'section':None,'section_status':'provider_not_reported','native_metadata_complete':False,'is_subtask':False,'parent_id':None,'has_subtasks':False,'hierarchy_complete':False,'children_status':'provider_reported','child_ids':[],'children_complete':False,'children_fetch_status':'provider_reported'}
            return result
        self.reader.read=enriched
    def apply(self,item):
        require_write('reminders',item);assert item['operation']=='set_flagged';self.flag=item['fields']['flagged']
        return {'id':item['id'],'source_id':'selected','list_id':'list'}
adapter=Adapter();native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
