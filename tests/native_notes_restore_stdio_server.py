"""Real restore adapter and MCP policies with isolated synthetic Notes objects."""
import atexit,copy,json,os,sys
from pathlib import Path
from unittest.mock import patch
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:blocked.append(event);raise AssertionError('Synthetic restore server forbids network/helpers')
sys.addaudithook(audit);atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from test_native_notes_restore import Adapter
from icloud_for_agents import native_tools,native_note_store,credentials
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
adapter=Adapter();adapter.hide_empty=True;notes={identity:dict(copy.deepcopy(adapter.reader.note),id=identity) for identity in ('note-one','note-two','note-three')};read=adapter.reader.read
# Each exact ID has independent position and content; no synthetic write-back reset.
def selected_read(action,**fields):
    if action=='folder_get' and fields.get('folder_id')=='folder':
        remaining=[note for note in notes.values() if note['folder_id']=='folder']
        if remaining:adapter.reader.note=remaining[0]
    if action in {'get','attachments','contains'} and fields.get('id') in notes:adapter.reader.note=notes[fields['id']]
    result=read(action,**fields)
    if action=='attachments':result['attachments']=[dict(value,note_id=fields['id']) for value in result['attachments']]
    return result
adapter.reader.read=selected_read
native_note_store.folder_role=adapter.role;native_note_store.recovery_folder=adapter.recovery
native_note_store.note_location=lambda identity,sources:{'id':identity,'folder_id':notes[identity]['folder_id'],'source_id':'selected','content_read':False,'database_writes':False}
native_tools.NotesCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
