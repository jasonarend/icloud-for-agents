"""Actual native section membership adapter and MCP permissions with in-memory provider state."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic rule server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from test_native_reminder_sections import Adapter
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
adapter=Adapter()
from copy import deepcopy
from icloud_for_agents import native_reminder_sections,native_reminder_section_store
native_reminder_sections.read_store=lambda source,list_id:deepcopy(adapter.reader.map)
native_reminder_section_store.read=native_reminder_sections.read_store
native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
