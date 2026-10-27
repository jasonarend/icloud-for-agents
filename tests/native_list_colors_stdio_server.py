"""Actual list-color adapter and MCP permissions with in-memory provider state."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic rule server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from test_native_list_colors import Adapter
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
adapter=Adapter()
# Two explicit provider-drop fixtures; corrections use the same production adapter.
from icloud_for_agents.native_colors import validate_color
sentinels=[validate_color('#000011'),validate_color('#000022')];seen=[];original_apply=adapter.apply
def apply(item):
    color=item['fields'].get('color')
    adapter.ignore=color in sentinels and color not in seen
    if adapter.ignore:seen.append(color)
    try:return original_apply(item)
    finally:adapter.ignore=False
adapter.apply=apply
native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
