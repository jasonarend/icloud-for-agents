"""Real MCP subtask plans and grants backed by an isolated synthetic provider."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic subtask server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_access import require_write
from test_native_reminders import Reader
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-native-subtask'
    def __init__(self):
        self.reader=Reader();self.reader.item.update(public_metadata_complete=True,has_recurrence=False,recurrence_rules=[],has_alarms=False,alarms=[],created=12345,url=None,location=None)
        self.children={};read=self.reader.read
        def enriched(action,**fields):
            if action=='get' and fields.get('id') in self.children:
                identity=fields['id'];return {'item':{'id':identity,'title':self.children[identity],'source_id':'selected','list_id':'list','completed':False,'priority':0,'notes':'','due':None,'start':None,'has_alarms':False,'has_recurrence':False,'alarms':[],'recurrence_rules':[],'url':None,'location':None,'public_metadata_complete':True,'native_metadata':{'is_subtask':True,'parent_id':'created','flagged':False,'urgent':False,'tags_complete':True,'tags':[]}}}
            if action=='get' and fields.get('id')!='created':raise SafetyError('Unknown synthetic parent')
            result=read(action,**fields)
            if action=='get' and fields.get('include_native_urgent'):
                result['item']['native_metadata']={**{k:result['item'][k] for k in ('id','source_id','list_id')},'coverage':['urgent'],'urgent':False}
            if action=='get' and fields.get('include_native_metadata'):
                result['item']['native_metadata']={**{k:result['item'][k] for k in ('id','source_id','list_id')},'flagged':False,'flag_state':0,'tags':[],'tags_complete':True,'coverage':['flagged','tags','hierarchy','children','section'],'section':None,'section_status':'provider_not_reported','native_metadata_complete':False,'is_subtask':False,'parent_id':None,'has_subtasks':bool(self.children),'hierarchy_complete':False,'children_status':'provider_reported','child_ids':sorted(self.children),'children_complete':False,'children_fetch_status':'provider_reported'}
            return result
        self.reader.read=enriched
    def apply(self,item):
        require_write('reminders',item)
        if item['operation']=='create_section':
            self.reader.sections.append({'id':item['section_id'],'title':item['fields']['title']});self.reader.sections.sort(key=lambda row:row['id'])
            return {'id':item['section_id'],'source_id':'selected','list_id':'list'}
        assert item['operation']=='create_subtask';self.children[item['child_id']]=item['fields']['title']
        return {'id':item['child_id'],'parent_id':item['id'],'source_id':'selected','list_id':'list'}
adapter=Adapter()
if os.environ.get('BRIDGE_TEST_VERIFY_FAILURE')=='1':
    original_verify=adapter.verify;verify_calls=[0]
    def delayed_verify(item,outcome):
        verify_calls[0]+=1
        return False if verify_calls[0]==1 else original_verify(item,outcome)
    adapter.verify=delayed_verify
native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
