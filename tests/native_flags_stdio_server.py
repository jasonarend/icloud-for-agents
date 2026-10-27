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
        if 'due_date' in request:assert request['due_date']=='2026-10-27' and request['timezone']=='UTC' and request['completed'] is False and request['list_id']=='list'
        return {'protocol_version':1,'items':[],'done':False,'next_offset':1,'pagination_consistency':'live_offset_may_shift','native_flag_filter':{'scanned':1,'matched':0,'observation_consistency':'separate_provider_reads'}}
    item={'id':request.get('id','synthetic-task'),'list_id':'list','source_id':'selected','content_complete':False}
    if request.get('include_native_metadata'):
        item['native_metadata']={'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'flagged':True,'flag_state':1,'coverage':['flagged'],'native_metadata_complete':False,'observation_consistency':'separate_provider_reads'}
    if request.get('include_native_tags'):
        metadata=item.setdefault('native_metadata',{'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'coverage':[],'native_metadata_complete':False})
        metadata.update(tags=[{'id':'tag','name':'Project','native_type':0}],tags_complete=True)
        metadata['coverage'].append('tags')
    if request.get('include_native_hierarchy'):
        metadata=item.setdefault('native_metadata',{'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'coverage':[],'native_metadata_complete':False})
        if item['id']=='synthetic-child':metadata.update(is_subtask=True,parent_id='synthetic-parent',has_subtasks=None,hierarchy_complete=False,children_status='unavailable')
        else:metadata.update(is_subtask=False,parent_id=None,has_subtasks=False,hierarchy_complete=False,children_status='provider_reported')
        metadata['coverage'].append('hierarchy')
    if request.get('include_native_children'):
        metadata=item.setdefault('native_metadata',{'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'coverage':[],'native_metadata_complete':False})
        metadata.update(child_ids=None if item['id']=='synthetic-child' else ['synthetic-child'],children_complete=False,children_fetch_status='unavailable' if item['id']=='synthetic-child' else 'provider_reported')
        metadata['coverage'].append('children')
    return {'protocol_version':1,'item':item}
NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
