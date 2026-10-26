"""Synthetic native providers behind the production MCP handlers; no real OS access."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic native server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
from test_native_once import Adapter
from icloud_for_agents import native_tools
notes=Adapter('notes');reminders=Adapter('reminders')
native_tools.NotesCreateAdapter=lambda:notes
native_tools.RemindersCreateAdapter=lambda:reminders
from icloud_for_agents.server import main
main()
