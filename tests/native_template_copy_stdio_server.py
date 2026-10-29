"""Actual MCP write admission over isolated synthetic Smart List objects."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic Smart List server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from test_native_template_copy import Adapter
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
from icloud_for_agents import native_template_copy
adapter=Adapter();
from test_native_template_copy import SOURCE,TEMPLATE,ROOT
native_template_copy.outline=lambda *a,**k:{'title':'Synthetic template','done':True,'total_items':1,'items':[{'item_id':ROOT,'parent_item_id':None,'title':'Synthetic task','priority':1}]}
native_template_copy.details=lambda *a,**k:{'source_id':SOURCE,'template_id':TEMPLATE,'item_id':ROOT,'parent_item_id':None,'title':'Synthetic task','notes':'Synthetic notes','priority':1};native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
