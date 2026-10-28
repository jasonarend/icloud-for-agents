"""Production Notes planning and MCP authorization with an isolated synthetic provider."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic Notes server forbids network and helper launches')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools
from icloud_for_agents.native_notes import NotesCreateAdapter
from icloud_for_agents.native_access import require_write
from test_native_notes import Reader
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
class Adapter(NotesCreateAdapter):
    backend_id='synthetic-large-note'
    def __init__(self):self.reader=Reader();self.count=0
    def apply(self,item):
        require_write('notes',item);self.count+=1;identity='synthetic-created-'+str(self.count)
        self.reader.note.update(id=identity,title=item['fields']['title'],html='<div>'+item['fields']['title']+'</div>'+item['fields']['html'],text=item['fields']['expected_text'])
        return {'id':identity,'source_id':'selected','folder_id':'folder'}
adapter=Adapter();native_tools.NotesCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
