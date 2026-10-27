"""Actual native metadata read tools with synthetic provider data, no OS or network."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic flags server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
from icloud_for_agents.native_read import NativeReader
def dispatch(self,request,config):
    assert self.name=='reminders' and request['source_ids']==['selected']
    if request['action']=='query':
        assert type(request.get('flagged')) is bool and request['limit']==1
        return {'protocol_version':1,'items':[],'done':False,'next_offset':1,'native_flag_filter':{'scanned':1,'matched':0}}
    item={'id':'synthetic-task','list_id':'list','source_id':'selected','content_complete':False}
    if request.get('include_native_metadata'):
        item['native_metadata']={'flagged':True,'flag_state':1,'coverage':['flagged'],'native_metadata_complete':False,'observation_consistency':'separate_provider_reads'}
    if request.get('include_native_tags'):
        metadata=item.setdefault('native_metadata',{'coverage':[],'native_metadata_complete':False})
        metadata.update(tags=[{'id':'tag','name':'Project','native_type':0}],tags_complete=True)
        metadata['coverage'].append('tags')
    return {'protocol_version':1,'item':item}
NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
