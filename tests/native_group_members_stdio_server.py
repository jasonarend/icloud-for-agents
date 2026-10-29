"""Synthetic fixed-protocol group reads; real native reader validation and MCP."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:blocked.append(event);raise AssertionError('No network or real helpers in synthetic group reads')
sys.addaudithook(audit);atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import native_read,credentials
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
SOURCE='11111111-1111-4111-8111-111111111111';GROUP='22222222-2222-4222-8222-222222222222'
LIST='33333333-3333-4333-8333-333333333333';SMART='44444444-4444-4444-8444-444444444444'
def dispatch(self,request,config):
    assert request['action']=='group_lists',request['action'];assert request['source_id']==SOURCE
    smart=request.get('include_smart_lists',False);rows=[{'id':LIST,'title':'Synthetic physical child','is_group':False}]
    if smart:rows=[dict(rows[0],kind='list'),{'id':SMART,'title':'Synthetic Smart List child','is_group':False,'kind':'smart_list'}]
    start=request['offset'];page=rows[start:start+request['limit']];result={'protocol_version':1,'source_id':SOURCE,'group_id':request['group_id'],'lists':page,'done':start+len(page)>=len(rows),'next_offset':start+len(page),'pagination_consistency':'live_offset_may_shift','membership_complete':False,'ordering_verified':False,'data_mutated':False,'backend':'reminderkit-group-lists-v3' if smart else 'reminderkit-group-lists-v2'}
    if smart:result['custom_smart_lists_included']=True
    if request['group_id']==SMART:result['backend']='reminderkit-group-lists-v2'
    return result
native_read.NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
