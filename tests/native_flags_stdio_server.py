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
    if request['action']=='template_sections':
        return {'protocol_version':1,'source_id':'selected','id':request['id'],'sections':[{'id':'55555555-5555-4555-8555-555555555555','title':'Synthetic section'}],'sections_complete':False,'ordering_verified':False,'membership_verified':False,'data_mutated':False,'backend':'reminderkit-template-sections-v1'}
    if request['action']=='group_lists':
        return {'protocol_version':1,'source_id':'selected','group_id':request['group_id'],'lists':[{'id':'55555555-5555-4555-8555-555555555555','title':'Synthetic child'}],'membership_complete':False,'ordering_verified':False,'data_mutated':False,'backend':'reminderkit-group-lists-v1'}
    if request['action']=='list_parent':
        return {'protocol_version':1,'source_id':'selected','list_id':request['list_id'],'parent':{'id':'55555555-5555-4555-8555-555555555555','title':'Synthetic group'},'parent_status':'provider_reported','hierarchy_complete':False,'data_mutated':False,'backend':'reminderkit-list-parent-v1'}
    if request['action']=='smart_lists':
        return {'protocol_version':1,'source_id':'selected','smart_lists':[{'id':'33333333-3333-4333-8333-333333333333','title':'Synthetic'}],'next_offset':request['offset']+1,'done':True,'pagination_consistency':'live_offset_may_shift','filter_rules_complete':False,'data_mutated':False,'backend':'reminderkit-smart-lists-v1'}
    if request['action']=='templates':
        return {'protocol_version':1,'source_id':'selected','templates':[{'id':'33333333-3333-4333-8333-333333333333','title':'Synthetic'}],'next_offset':request['offset']+1,'done':True,'pagination_consistency':'live_offset_may_shift','template_contents_complete':False,'data_mutated':False,'backend':'reminderkit-templates-v1'}
    if request['action']=='default_list':
        return {'protocol_version':1,'state':'available','list':{'id':'list','title':'Synthetic list','source_id':'selected','writable':True,'color':None}}
    if request['action']=='list_features':
        result={'protocol_version':1,'source_id':'selected','list_id':request['list_id'],'grocery_categorization':True,'grocery_shopping_session':False,'is_group':False,'shared':True,'shared_to_me':False,'can_be_shared':True,'sharing_metadata_complete':False,'list_type_verified':False,'data_mutated':False,'backend':'reminderkit-list-features-v2'}
        if request.get('include_pinned'):result.update(pinned=True,backend='reminderkit-list-features-v3')
        return result
    if request['action']=='list_sections':
        return {'protocol_version':1,'source_id':'selected','list_id':request['list_id'],'sections':[{'id':'11111111-1111-4111-8111-111111111111','title':'Synthetic section'}],'ordering_verified':False,'membership_verified':False,'data_mutated':False}
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
    if request['action']=='query' and ('completed_from' in request or 'completed_before' in request):
        result={'protocol_version':1,'items':[],'done':True,'next_offset':request['offset'],'pagination_consistency':'live_offset_may_shift','completion_filter':{'from':request.get('completed_from'),'before':request.get('completed_before'),'semantics':'utc_inclusive_from_exclusive_before'}}
        evidence={'scanned':0,'matched':0,'observation_consistency':'separate_provider_reads'}
        for field,key in (('flagged','native_flag_filter'),('urgent','native_urgent_filter'),('tag','native_tag_filter'),('has_current_assignment','native_assignment_filter'),('has_native_attachments','native_attachment_filter')):
            if field in request:result[key]=evidence
        return result
    if request['action']=='query':
        assert (type(request.get('flagged')) is bool or type(request.get('urgent')) is bool or isinstance(request.get('tag'),str) or type(request.get('has_current_assignment')) is bool or type(request.get('has_native_attachments')) is bool) and request['limit']==1
        if 'due_date' in request:assert request['due_date']=='2026-10-27' and request['timezone']=='UTC' and request['completed'] is False and request['list_id']=='list'
        result={'protocol_version':1,'items':[],'done':False,'next_offset':1,'pagination_consistency':'live_offset_may_shift'}
        evidence={'scanned':1,'matched':0,'observation_consistency':'separate_provider_reads'}
        if 'has_native_attachments' in request:result['native_attachment_filter']=evidence
        if 'has_current_assignment' in request:result['native_assignment_filter']=evidence
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
    if request.get('include_native_section'):
        metadata=item.setdefault('native_metadata',{'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'coverage':[],'native_metadata_complete':False})
        metadata.update(section={'id':'11111111-1111-4111-8111-111111111111','title':'Synthetic section'},section_status='provider_reported');metadata['coverage'].append('section')
    if request.get('include_native_early'):
        metadata=item.setdefault('native_metadata',{'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'coverage':[],'native_metadata_complete':False})
        metadata.update(early_alerts=[{'native_unit':0,'native_count':-5},{'native_unit':3,'native_count':-1},{'native_unit':4,'native_count':-1},{'native_unit':9,'native_count':-1}],early_payload_fingerprint='a'*64);metadata['coverage'].append('early')
    if request.get('include_native_assignment'):
        metadata=item.setdefault('native_metadata',{'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'coverage':[],'native_metadata_complete':False})
        metadata.update(current_assignment_reported=item['id']=='synthetic-assigned',assignment_record_count=int(item['id']=='synthetic-assigned'),assignment_metadata_complete=False);metadata['coverage'].append('assignment')
    if request.get('include_native_attachments'):
        metadata=item.setdefault('native_metadata',{'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'coverage':[],'native_metadata_complete':False})
        metadata.update(attachments=[{'id':'synthetic-attachment','kind':'file','uti':'public.data'}] if item['id']=='synthetic-attached' else [],attachment_metadata_complete=False);metadata['coverage'].append('attachments')
    return {'protocol_version':1,'item':item}
NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
