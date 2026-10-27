"""Actual read tools and projection with synthetic provider data, no OS or network."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic Markdown server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
from icloud_for_agents.native_read import NativeReader
def dispatch(self,request,config):
    if request['action']=='folders':return {'protocol_version':1,'folders':[{'id':'folder','title':'Synthetic','source_id':'selected'}],'done':True,'next_offset':1}
    if request['action']=='contains':return {'protocol_version':1,'exists':True,'folder_id':request['folder_id'],'source_id':'selected'}
    return {'protocol_version':1,'item':{'id':'synthetic-note','folder_id':'folder','source_id':'selected','html':'<h1>Product spec</h1><p><strong>Decision</strong></p><object><table><tr><td>Priority</td><td>High</td></tr></table></object>','truncated':False,'locked':False}}
NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
