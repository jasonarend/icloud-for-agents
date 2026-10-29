"""Real MCP read handling over a synthetic, isolated template SQLite store."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic template server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_read,native_reminder_template_store as store
from test_native_template_outline import Reader,create,STORE
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
root=store.stores_root();root.mkdir(parents=True,mode=0o700);create(root/('Data-'+STORE+'.sqlite'))
reader=Reader();native_read.NativeReader=lambda name:reader
store.platform.system=lambda:'Darwin'
store.platform.mac_ver=lambda:('27.0',('','',''),'')
from icloud_for_agents.server import main
main()
