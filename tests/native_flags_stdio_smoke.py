"""Optional flag/tag reads through real MCP stdio; synthetic provider only."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run():
    with tempfile.TemporaryDirectory(prefix='reminders-flags-stdio-') as directory:
        home=Path(directory);audit=home/'audit.json';env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'local-receipt','ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'0','ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':['selected'],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}}),'ICLOUD_RUNTIME_ROOT':str(home/'runtime')}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_flags_stdio_server.py')],env=env,cwd=home,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
            def rpc(method,params):
                nonlocal sequence
                sequence+=1;current=sequence;process.stdin.write((json.dumps({'jsonrpc':'2.0','id':current,'method':method,'params':params})+'\n').encode());process.stdin.flush();deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if select.select([process.stdout],[],[],1)[0]:
                        line=process.stdout.readline()
                        if not line:raise AssertionError('Synthetic native metadata server exited')
                        result=json.loads(line)
                        if result.get('id')==current:return result
                raise AssertionError('Synthetic MCP timeout')
            def call(args):return rpc('tools/call',{'name':'reminders_get','arguments':args})['result']
            def data(value):
                assert not value.get('isError'),value
                return value.get('structuredContent') or json.loads(value['content'][0]['text'])
            try:
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-flags','version':'1'}});process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                tools=rpc('tools/list',{})['result']['tools'];tool=next(t for t in tools if t['name']=='reminders_get');assert tool['annotations']['readOnlyHint'] is True
                default_tool=next(t for t in tools if t['name']=='reminders_default_list');assert default_tool['annotations']['readOnlyHint'] is True
                default=data(rpc('tools/call',{'name':'reminders_default_list','arguments':{}})['result']);assert default['state']=='available' and default['list']['source_id']=='selected'
                sections_tool=next(t for t in tools if t['name']=='reminders_list_sections');assert sections_tool['annotations']['readOnlyHint'] is True
                sections=data(rpc('tools/call',{'name':'reminders_list_sections','arguments':{'list_id':'list'}})['result']);assert sections['sections'][0]['title']=='Synthetic section' and sections['membership_verified'] is False
                schema=tool['inputSchema']['properties']['include_native_metadata'];assert schema['type']=='boolean' and schema['default'] is False
                args={'id':'synthetic-task'};native=data(call(args));assert 'native_metadata' not in native['item']
                projected=data(call(dict(args,include_native_metadata=True)));item=projected['item'];metadata=item.pop('native_metadata');assert item==native['item'];assert metadata['flagged'] is True and metadata['coverage']==['flagged'] and metadata['native_metadata_complete'] is False
                assert call(dict(args,include_native_metadata='execute-script')).get('isError')
                tag_schema=tool['inputSchema']['properties']['include_native_tags'];assert tag_schema['type']=='boolean' and tag_schema['default'] is False
                tags=data(call(dict(args,include_native_tags=True)))['item']['native_metadata'];assert tags['tags'][0]['name']=='Project' and tags['coverage']==['tags'] and tags['tags_complete'] is True
                combined=data(call(dict(args,include_native_tags=True,include_native_metadata=True)))['item']['native_metadata'];assert combined['coverage']==['flagged','tags']
                assert call(dict(args,include_native_tags='execute-script')).get('isError')
                hierarchy_schema=tool['inputSchema']['properties']['include_native_hierarchy'];assert hierarchy_schema['type']=='boolean' and hierarchy_schema['default'] is False
                hierarchy=data(call(dict(args,include_native_hierarchy=True)))['item']['native_metadata'];assert hierarchy['is_subtask'] is False and hierarchy['hierarchy_complete'] is False and hierarchy['coverage']==['hierarchy']
                child=data(call({'id':'synthetic-child','include_native_hierarchy':True}))['item']['native_metadata'];assert child['is_subtask'] is True and child['parent_id']=='synthetic-parent' and child['has_subtasks'] is None and child['children_status']=='unavailable'
                assert call(dict(args,include_native_hierarchy='invalid')).get('isError')
                schema=tool['inputSchema']['properties']['include_native_children'];assert schema['type']=='boolean' and schema['default'] is False
                children=data(call(dict(args,include_native_children=True)))['item']['native_metadata'];assert children['child_ids']==['synthetic-child'] and children['children_complete'] is False and children['coverage']==['children']
                unknown=data(call({'id':'synthetic-child','include_native_children':True}))['item']['native_metadata'];assert unknown['child_ids'] is None and unknown['children_fetch_status']=='unavailable'
                assert call(dict(args,include_native_children='invalid')).get('isError')
                urgent=data(rpc('tools/call',{'name':'reminders_get','arguments':{'id':'synthetic-task','include_native_urgent':True}})['result']);assert urgent['item']['native_metadata']['urgent'] is False
                assert rpc('tools/call',{'name':'reminders_get','arguments':{'id':'synthetic-task','include_native_urgent':1}})['result'].get('isError')
                combined=data(rpc('tools/call',{'name':'reminders_get','arguments':{'id':'synthetic-task','include_native_tags':True,'include_native_urgent':True}})['result']);assert combined['item']['native_metadata']['coverage']==['tags','urgent']
                attachments=data(call({'id':'synthetic-attached','include_native_attachments':True,'include_native_assignment':True}))['item']['native_metadata'];assert attachments['attachments'][0]['kind']=='file' and attachments['coverage']==['assignment','attachments'] and attachments['attachment_metadata_complete'] is False
                assignment=data(call({'id':'synthetic-task','include_native_assignment':True}))['item']['native_metadata'];assert assignment['current_assignment_reported'] is False and assignment['assignment_record_count']==0 and assignment['assignment_metadata_complete'] is False
                assigned=data(call({'id':'synthetic-assigned','include_native_assignment':True,'include_native_urgent':True}))['item']['native_metadata'];assert assigned['current_assignment_reported'] is True and assigned['assignment_record_count']==1 and assigned['coverage']==['urgent','assignment']
                early_item=data(call({'id':'synthetic-task','include_native_early':True}))['item'];early=early_item['native_metadata'];assert [a['native_unit'] for a in early['early_alerts']]==[0,3,4,9] and early['coverage']==['early'];assert [a['unit'] for a in early_item['early_reminders']]==['minutes','weeks','months',None]
                features=data(rpc('tools/call',{'name':'reminders_list_features','arguments':{'list_id':'list'}})['result']);assert features['grocery_categorization'] is True and features['shared'] is True and features['sharing_metadata_complete'] is False and features['list_type_verified'] is False
                pinned=data(rpc('tools/call',{'name':'reminders_list_features','arguments':{'list_id':'list','include_pinned':True}})['result']);assert pinned['pinned'] is True and pinned['backend']=='reminderkit-list-features-v3' and 'pinned' not in features
                section=data(call({'id':'synthetic-task','include_native_section':True}))['item']['native_metadata'];assert section['section']['title']=='Synthetic section' and section['coverage']==['section']
                recovery=next(t for t in tools if t['name']=='reminders_recovery_lookup');assert recovery['annotations']['readOnlyHint'] is True
                lookup=data(rpc('tools/call',{'name':'reminders_recovery_lookup','arguments':{'id':'synthetic-task','source_id':'selected'}})['result']);assert lookup['found_as_root'] is True and lookup['data_mutated'] is False
                assert rpc('tools/call',{'name':'reminders_recovery_lookup','arguments':{'id':'synthetic-task','source_id':'foreign'}})['result'].get('isError')
                discovery=next(t for t in tools if t['name']=='reminders_recovery_list');assert discovery['annotations']['readOnlyHint'] is True
                page=data(rpc('tools/call',{'name':'reminders_recovery_list','arguments':{'source_id':'selected'}})['result']);assert page['items'][0]['title']=='Synthetic recovery title' and page['done'] is True
                assert rpc('tools/call',{'name':'reminders_recovery_list','arguments':{'source_id':'foreign'}})['result'].get('isError')
                atomic=next(t for t in tools if t['name']=='reminders_recovery_tree');assert atomic['annotations']['readOnlyHint'] is True
                observed=data(rpc('tools/call',{'name':'reminders_recovery_tree','arguments':{'id':'22222222-2222-4222-8222-222222222222','source_id':'selected'}})['result']);assert len(observed['nodes'])==2 and observed['hierarchy_complete'] is False and observed['observation_consistency']=='single_provider_view'
                assert rpc('tools/call',{'name':'reminders_recovery_tree','arguments':{'id':'22222222-2222-4222-8222-222222222222','source_id':'foreign'}})['result'].get('isError')
                recovery_tree=next(t for t in tools if t['name']=='reminders_inspect_recovery_hierarchy');assert recovery_tree['annotations']['readOnlyHint'] is True
                tree=data(rpc('tools/call',{'name':'reminders_inspect_recovery_hierarchy','arguments':{'id':'synthetic-child','source_id':'selected'}})['result']);assert tree['recovery_root_id']=='synthetic-parent' and len(tree['nodes'])==2 and tree['hierarchy_complete'] is False
                hierarchy_tool=next(t for t in tools if t['name']=='reminders_inspect_hierarchy');assert hierarchy_tool['annotations']['readOnlyHint'] is True
                tree=data(rpc('tools/call',{'name':'reminders_inspect_hierarchy','arguments':{'id':'synthetic-parent'}})['result']);assert len(tree['nodes'])==2 and tree['nodes'][1]['parent_id']=='synthetic-parent' and tree['nodes'][1]['title']=='Synthetic task' and tree['nodes'][1]['completed'] is False and tree['hierarchy_complete'] is False
                assert rpc('tools/call',{'name':'reminders_inspect_hierarchy','arguments':{'id':'synthetic-parent','max_nodes':21}})['result'].get('isError')
                query=next(t for t in tools if t['name']=='reminders_query');assert query['annotations']['readOnlyHint'] is True
                assert 'YYYY-MM-DDTHH:MM:SSZ' in query['description'] and 'inclusive' in query['description'] and 'exclusive' in query['description']
                completion=data(rpc('tools/call',{'name':'reminders_query','arguments':{'completed_from':'2026-10-27T00:00:00Z','completed_before':'2026-10-28T00:00:00Z'}})['result']);assert completion['completion_filter']['semantics']=='utc_inclusive_from_exclusive_before' and completion['items']==[]
                assert rpc('tools/call',{'name':'reminders_query','arguments':{'completed':False,'completed_from':'2026-10-27T00:00:00Z'}})['result'].get('isError')
                combined_range=data(rpc('tools/call',{'name':'reminders_query','arguments':{'flagged':True,'completed_from':'2026-10-27T00:00:00Z'}})['result']);assert combined_range['native_flag_filter']['scanned']==0 and combined_range['completion_filter']['from']=='2026-10-27T00:00:00Z'

                urgent_page=data(rpc('tools/call',{'name':'reminders_query','arguments':{'urgent':True,'limit':1}})['result']);assert urgent_page['native_urgent_filter']['scanned']==1 and urgent_page['done'] is False
                urgent_daily=data(rpc('tools/call',{'name':'reminders_daily','arguments':{'date':'2026-10-27','timezone':'UTC','urgent':False,'limit':1,'list_id':'list'}})['result']);assert urgent_daily['native_urgent_filter']['scanned']==1
                flag_query=data(rpc('tools/call',{'name':'reminders_query','arguments':{'flagged':True,'limit':1}})['result']);assert flag_query['items']==[] and flag_query['done'] is False and flag_query['next_offset']==1
                assert rpc('tools/call',{'name':'reminders_query','arguments':{'flagged':'invalid'}})['result'].get('isError')
                assert 'tag' in query['inputSchema']['properties']
                tagged=data(rpc('tools/call',{'name':'reminders_query','arguments':{'tag':'Project','flagged':True,'limit':1}})['result']);assert tagged['native_tag_filter']==tagged['native_flag_filter'] and tagged['next_offset']==1
                assert rpc('tools/call',{'name':'reminders_query','arguments':{'tag':''}})['result'].get('isError')
                tagged_daily=data(rpc('tools/call',{'name':'reminders_daily','arguments':{'date':'2026-10-27','timezone':'UTC','tag':'Project','limit':1,'list_id':'list'}})['result']);assert tagged_daily['native_tag_filter']['scanned']==1
                daily=data(rpc('tools/call',{'name':'reminders_daily','arguments':{'date':'2026-10-27','timezone':'UTC','flagged':True,'limit':1,'list_id':'list'}})['result']);assert daily['items']==[] and daily['done'] is False and daily['next_offset']==1

            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
    report={'status':'passed','real_mcp_stdio':True,'synthetic_provider_only':True,'checks':['default_compatibility','boolean_schema','optional_flag_and_tag_metadata','public_metadata_retained','read_only_annotations','invalid_selector_refused','no_os_network_or_credentials']};print(json.dumps(report));return report
if __name__=='__main__':run()
