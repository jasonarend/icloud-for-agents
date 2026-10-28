"""Actual read tools and projection with synthetic provider data, no OS or network."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic Markdown server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
from icloud_for_agents.native_read import NativeReader
def dispatch(self,request,config):
    if request['action']=='default_folder':return {'protocol_version':1,'folder':{'id':'folder','source_id':'selected','parent_id':'selected','title':'Synthetic','shared':False,'role':'default_folder'}}
    if request['action']=='folders':return {'protocol_version':1,'folders':[{'id':'folder','title':'Synthetic','source_id':'selected','parent_id':'selected'}],'done':True,'next_offset':1,'pagination_consistency':'live_offset_may_shift'}
    if request['action']=='contains':return {'protocol_version':1,'exists':True,'folder_id':request['folder_id'],'source_id':'selected'}
    if request['action']=='attachments':
        meta={'created':{'status':'available','value':'2026-01-01T00:00:00.000Z'},'modified':{'status':'available','value':None},'url':{'status':'unavailable','value':None},'shared':{'status':'available','value':False}}
        row={'id':'attachment','name':None,'content_identifier':'cid:synthetic','note_id':'synthetic-note'}
        if request.get('include_attachment_metadata'):row['native_metadata']=meta
        result={'protocol_version':1,'attachments':[row],'source_id':'selected','folder_id':'folder','note_id':'synthetic-note','done':True,'next_offset':1,'content_downloaded':False,'metadata_complete':False}
        if request.get('include_attachment_metadata'):result.update(metadata_requested=True)
        return result
    if request['action']=='search':
        if request.get('match_scope')=='title':
            rows=[] if request.get('locked') is True or request.get('shared') is True else [{'id':'synthetic-note','title':'Synthetic Title','source_id':'selected','folder_id':'folder','locked':False,'excerpt':'','content_complete':False}]
            result={'protocol_version':1,'items':rows,'table_projection_candidates':[],'match_scope':'title','done':True,'next_offset':1}
            if 'locked' in request:result['locked_filter']=request['locked']
            if 'shared' in request:
                result['shared_filter']=request['shared']
                for row in rows:row['shared']=False
            if 'created_from' in request or 'created_before' in request:
                result['created_filter']={'from':request.get('created_from'),'before':request.get('created_before'),'semantics':'utc_inclusive_from_exclusive_before'}
                for row in rows:row['created']='2026-10-27T12:30:00.000Z'
            if 'modified_from' in request or 'modified_before' in request:
                result['modified_filter']={'from':request.get('modified_from'),'before':request.get('modified_before'),'semantics':'utc_inclusive_from_exclusive_before'}
                for row in rows:row['modified']='2026-10-27T12:30:00.000Z'
            return result
        assert request.get('modified_from')=='2026-10-27T12:00:00Z' and request.get('modified_before')=='2026-10-27T13:00:00Z'
        return {'protocol_version':1,'items':[],'table_projection_candidates':[{'id':'synthetic-note','title':'Synthetic','source_id':'selected','folder_id':'folder','locked':False,'html':'<table><tr><td>Priority</td></tr></table>','truncated':False,'modified':'2026-10-27T12:30:00.000Z'}],'done':True,'next_offset':1,'modified_filter':{'from':request['modified_from'],'before':request['modified_before'],'semantics':'utc_inclusive_from_exclusive_before'}}
    if request['action']=='text_page':
        identity=request['id'];base={'protocol_version':1,'id':identity,'folder_id':'folder','source_id':'foreign' if identity=='foreign-note' else 'selected','data_mutated':False}
        if identity=='synthetic-locked':return {**base,'locked':True,'content_available':False}
        modified='2026-10-27T12:30:00Z'
        if request.get('expected_modified') not in (None,modified):return {'protocol_version':1,'error':'snapshot_changed'}
        text='Synthetic text café 🚀';raw=text.encode('utf-16-le');offset=request['offset'];end=min(len(raw)//2,offset+request['limit'])
        if identity=='oversized-note':end=min(len(raw)//2,end+2)
        if identity=='extra-ascii-note':end=min(len(raw)//2,end+1)
        if offset>len(raw)//2:return {'protocol_version':1,'error':'invalid_page'}
        try:page=raw[offset*2:end*2].decode('utf-16-le')
        except UnicodeDecodeError:end+=1;page=raw[offset*2:end*2].decode('utf-16-le')
        return {**base,'locked':False,'content_available':True,'text':page,'offset':offset,'next_offset':end,'done':end>=len(raw)//2,'source_truncated':False,'content_complete':False,'modified':modified,'pagination_consistency':'live_note_may_change','offset_unit':'utf16_code_units','request_id':'must-not-leak'}
    if request.get('id')=='synthetic-locked':return {'protocol_version':1,'item':{'id':'synthetic-locked','source_id':'selected','folder_id':'folder','locked':True,'content_available':False,'html':'must-not-leak'}}
    if request.get('id')=='foreign-note':return {'protocol_version':1,'item':{'id':'foreign-note','folder_id':'folder','source_id':'foreign','locked':False,'text':'inaccessible','truncated':False}}
    return {'protocol_version':1,'item':{'id':'synthetic-note','folder_id':'folder','source_id':'selected','html':'<h1>Product spec</h1><p><strong>Decision</strong></p><object><table><tr><td>Priority</td><td>High</td></tr></table></object>','truncated':False,'locked':False,'text':'Synthetic text café 🚀','modified':'2026-10-27T12:30:00Z'}}
NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
