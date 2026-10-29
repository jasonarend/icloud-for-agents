"""MCP file-insertion tests with isolated providers and no helper/network access."""
import atexit,copy,hashlib,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
 if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:blocked.append(event);raise AssertionError('Synthetic file server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools,native_reminder_file_create as module
from test_native_reminder_file_create import Adapter,ROOT
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
adapter=Adapter();third='66666666-6666-4666-8666-666666666666';adapter.reader.items[third]=dict(copy.deepcopy(adapter.reader.items[ROOT]),id=third,title='Synthetic third');adapter.reader.parents[third]=None;adapter.reader.flags[third]=False;adapter.reader.attachments[third]=[]
def export(identity,attachment,maximum,*,reader):
 raw=reader.raw[attachment];return {'id':identity,'source_id':reader.items[identity]['source_id'],'list_id':reader.items[identity]['list_id'],'attachment_id':attachment,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'uti':'public.plain-text','kind':'file','data_mutated':False}
module.export=export;native_tools.RemindersCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
