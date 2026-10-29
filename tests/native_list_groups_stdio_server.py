"""Actual native root-group adapter and MCP permissions with in-memory provider state."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic rule server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from test_native_list_groups import Adapter
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
adapter=Adapter()
if os.environ.get('BRIDGE_TEST_POPULATED_GROUP')=='1':
    existing='55555555-5555-4555-8555-555555555555';smart='66666666-6666-4666-8666-666666666666'
    adapter.reader.inventory['account_order'].extend([existing,smart])
    adapter.reader.children['lists']=[{'id':existing,'title':'Synthetic existing member','kind':'list','is_group':False},{'id':smart,'title':'Synthetic existing view','kind':'smart_list','is_group':False}]
    adapter.reader.children['next_offset']=2
native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
