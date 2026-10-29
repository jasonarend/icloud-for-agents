"""Real Notes adapter with synthetic metadata/content; refuses helpers and network."""
import atexit,json,os,sys
from pathlib import Path
from unittest.mock import PropertyMock,patch
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_tools,native_note_store
from icloud_for_agents.native_notes import NotesCreateAdapter
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.notes_content import project_html
from test_native_notes import Reader
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
reader=Reader();revision=[1];sequence=[0]
def role(identity,sources):
    assert sources==['selected'] and identity in {'folder','destination'}
    node=reader.folder if identity=='folder' else reader.destination
    return {'folder_id':identity,'source_id':'selected','title':node['title'],'storage_kind':'regular','native_folder_type':0,'marked_for_deletion':False,'has_smart_query':False,'revision':revision[0],'parent_pk':None,'schema_sha256':'synthetic-schema'}
native_note_store.folder_role=role
native_note_store.note_location=lambda identity,sources:{'id':identity,'folder_id':reader.note['folder_id'],'source_id':'selected'}
patch.object(NotesCreateAdapter,'backend_id',new_callable=PropertyMock,return_value='synthetic-readonly-role-notes').start()
adapter=NotesCreateAdapter();adapter.reader=reader
def apply(item):
    wire=mutation_request('notes',item)['change'];assert wire['folder_guard']=='verified_store_roles' and wire['before']['roles']
    sequence[0]+=1;revision[0]+=1
    html=item['fields']['html'] if item['operation']!='create_note' else '<div>'+item['fields']['title']+'</div>'+item['fields']['html']
    reader.note.update(id=item.get('id','created-'+str(sequence[0])),title=item['fields']['title'],html=html,text=project_html(html)['text'],folder_id=item['folder_id'],modified='2026-10-28T12:00:'+str(sequence[0]).zfill(2)+'Z')
    return {'id':reader.note['id'],'folder_id':item['folder_id'],'source_id':'selected'}
adapter.apply=apply;native_tools.NotesCreateAdapter=lambda:adapter
from icloud_for_agents.server import main
main()
