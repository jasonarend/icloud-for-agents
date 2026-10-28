"""Isolated read-only provider response; production scope/graph validation stays active."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:blocked.append(event);raise AssertionError('Synthetic graph server forbids real helpers/network')
sys.addaudithook(audit);atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import NativeReader
SOURCE='11111111-1111-4111-8111-111111111111';LIST='22222222-2222-4222-8222-222222222222';ROOT='33333333-3333-4333-8333-333333333333';CHILD='44444444-4444-4444-8444-444444444444'
def dispatch(self,request,config):
    if request['action']!='list_hierarchy' or request.get('list_id')!=LIST:raise SafetyError('Unknown synthetic list')
    return {'protocol_version':1,'source_id':SOURCE,'list_id':LIST,'nodes':[{'id':ROOT,'parent_id':None,'title':'Synthetic parent','completed':False},{'id':CHILD,'parent_id':ROOT,'title':'Synthetic child','completed':False}],'backend':'reminderkit-list-hierarchy-v1','data_mutated':False,'membership_verified':True,'hierarchy_complete':True,'observation_consistency':'separate_provider_reads','coverage':'native_list_and_subtasks_matching_eventkit'}
NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
