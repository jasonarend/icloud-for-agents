"""Real urgent correction handlers with a synthetic historical failed verification."""
import atexit,json,os
from pathlib import Path
from unittest.mock import patch
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic urgent correction forbids network/helpers')
import sys
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.native_changes import NativeChanges
from test_native_corrections import Adapter
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
account='synthetic@example.com';store=PlanStore();adapter=Adapter();adapter.fail=False;urgent=[False];read=adapter.reader.read
def enriched(action,**fields):
    result=read(action,**fields)
    if action=='get' and fields.get('include_native_urgent'):
        result['item']['native_metadata']={**{k:result['item'][k] for k in ('id','source_id','list_id')},'coverage':['urgent'],'urgent':urgent[0]}
    if action=='get' and fields.get('include_native_metadata'):
        result['item']['native_metadata']={**{k:result['item'][k] for k in ('id','source_id','list_id')},'coverage':['flagged','tags','hierarchy','children','section'],'section':None,'section_status':'provider_not_reported','flagged':False,'flag_state':0,'tags':[],'tags_complete':True,'is_subtask':False,'parent_id':None,'has_subtasks':False,'hierarchy_complete':False,'children_status':'provider_reported','child_ids':[],'children_complete':False,'children_fetch_status':'provider_reported'}
    return result
adapter.reader.read=enriched;calls=[0];verify=adapter.verify
def apply(item):
    calls[0]+=1
    if calls[0]>1 or os.environ['BRIDGE_TEST_TARGET']=='before':urgent[0]=item['fields']['urgent']
    return {'id':'task','source_id':'selected','list_id':'list'}
def verify_once(item,outcome):return False if calls[0]==1 else verify(item,outcome)
adapter.apply=apply;adapter.verify=verify_once;engine=NativeChanges('reminders',account,adapter,store)
with patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client'}):
    original=engine.prepare_once([{'operation':'set_urgent','source_id':'selected','id':'task','fields':{'urgent':True}}]);assert engine.execute(original['plan_id'])['status']=='failed_or_uncertain'
native_tools.RemindersCreateAdapter=lambda:adapter
Path(os.environ['BRIDGE_TEST_ORIGINAL']).write_text(json.dumps({'plan_id':original['plan_id']}))
from icloud_for_agents.server import main
main()
