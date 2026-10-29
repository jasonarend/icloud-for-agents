"""Synthetic byte export; real native-reader validation and MCP transport."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
 if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:blocked.append(event);raise AssertionError('No network or real helper in synthetic attachment export')
sys.addaudithook(audit);atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import native_read,credentials
from test_native_note_attachments import response
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
def dispatch(self,request,config):
 assert self.name=='notes' and request['action']=='attachment_export'
 value=response()
 if request['attachment_id']=='wrong-hash':value['sha256']='f'*64
 return value
native_read.NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
