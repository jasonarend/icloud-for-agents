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
    if request['action']=='recovery_list':
        assert request['source_id']=='selected' and request['limit']==20 and request['offset']==0
        return {'protocol_version':1,'source_id':'selected','view':'provider_recently_deleted_roots','pagination_consistency':'live_offset_may_shift','data_mutated':False,'done':True,'next_offset':1,'items':[{'id':'11111111-1111-4111-8111-111111111111','source_id':'selected','title':'Synthetic recovery title'}]}
    if request['action']=='recovery_tree':
        root='11111111-1111-4111-8111-111111111111';child='22222222-2222-4222-8222-222222222222'
        assert request['id']==child and request['source_id']=='selected'
        return {'protocol_version':1,'requested_id':child,'root_id':root,'source_id':'selected','view':'provider_recently_deleted','hierarchy_complete':False,'observation_consistency':'single_provider_view','title_observation_consistency':'separate_object_reads','data_mutated':False,'nodes':[{'id':root,'title':'Synthetic root','parent_id':None,'depth':0,'reported_child_ids':[child],'children_status':'provider_reported'},{'id':child,'title':'Synthetic child','parent_id':root,'depth':1,'reported_child_ids':[],'children_status':'provider_reported'}]}
    if request['action']=='recovery_lookup':
        assert request['source_id']=='selected'
        child=request['id']=='synthetic-child';root='synthetic-parent' if child else request['id'];children=['synthetic-child'] if request['id']=='synthetic-parent' else None
        return {'protocol_version':1,'id':request['id'],'source_id':'selected','found_as_root':not child,'found_in_view':True,'parent_id':root if child else None,'recovery_root_id':root,'view':'provider_recently_deleted','reported_child_ids':children,'children_status':'provider_reported' if children is not None else 'unavailable','children_complete':False,'data_mutated':False,'observation_consistency':'separate_provider_reads'}
    if request['action']=='query':
        assert (type(request.get('flagged')) is bool or type(request.get('urgent')) is bool or isinstance(request.get('tag'),str)) and request['limit']==1
        if 'due_date' in request:assert request['due_date']=='2026-10-27' and request['timezone']=='UTC' and request['completed'] is False and request['list_id']=='list'
        result={'protocol_version':1,'items':[],'done':False,'next_offset':1,'pagination_consistency':'live_offset_may_shift'}
        evidence={'scanned':1,'matched':0,'observation_consistency':'separate_provider_reads'}
        if 'flagged' in request:result['native_flag_filter']=evidence
        if 'tag' in request:result['native_tag_filter']=evidence
        if 'urgent' in request:result['native_urgent_filter']=evidence
        return result
    item={'id':request.get('id','synthetic-task'),'title':'Synthetic task','completed':False,'list_id':'list','source_id':'selected','content_complete':False}
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
    if request.get('include_native_urgent'):
        metadata=item.setdefault('native_metadata',{'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'coverage':[],'native_metadata_complete':False})
        metadata['urgent']=False;metadata['coverage'].append('urgent')
    return {'protocol_version':1,'item':item}
NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
