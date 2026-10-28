"""Read-only representation through real MCP stdio; only synthetic provider content."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run():
    with tempfile.TemporaryDirectory(prefix='notes-markdown-stdio-') as directory:
        home=Path(directory);audit=home/'audit.json';env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'local-receipt','ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'0','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected'],'access_mode':'read-only'}}),'ICLOUD_RUNTIME_ROOT':str(home/'runtime')}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_markdown_stdio_server.py')],env=env,cwd=home,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
            def rpc(method,params):
                nonlocal sequence
                sequence+=1;current=sequence;process.stdin.write((json.dumps({'jsonrpc':'2.0','id':current,'method':method,'params':params})+'\n').encode());process.stdin.flush();deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if select.select([process.stdout],[],[],1)[0]:
                        line=process.stdout.readline()
                        if not line:raise AssertionError('Synthetic Markdown server exited')
                        result=json.loads(line)
                        if result.get('id')==current:return result
                raise AssertionError('Synthetic MCP timeout')
            def call(args):return rpc('tools/call',{'name':'notes_get','arguments':args})['result']
            def data(value):
                assert not value.get('isError'),value
                return value.get('structuredContent') or json.loads(value['content'][0]['text'])
            try:
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-markdown','version':'1'}});process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                tools=rpc('tools/list',{})['result']['tools'];tool=next(t for t in tools if t['name']=='notes_get');assert tool['annotations']['readOnlyHint'] is True
                assert next(t for t in tools if t['name']=='notes_export_observation')['annotations']['readOnlyHint'] is True
                assert next(t for t in tools if t['name']=='notes_inspect_exported_links')['annotations']['readOnlyHint'] is True
                assert next(t for t in tools if t['name']=='notes_format_list_markdown')['annotations']['readOnlyHint'] is True
                formatted=data(rpc('tools/call',{'name':'notes_format_list_markdown','arguments':{'markdown':'# Plan\n- First\n  1. Child'}})['result'])
                assert formatted['body']=='<h1>Plan</h1><ul><li>First<ol><li>Child</li></ol></li></ul>' and formatted['coverage']['native_checklists'] is False
                assert rpc('tools/call',{'name':'notes_format_list_markdown','arguments':{'markdown':'- [ ] Native checkbox'}})['result'].get('isError')
                default_tool=next(t for t in tools if t['name']=='notes_default_folder');assert default_tool['annotations']['readOnlyHint'] is True
                default=data(rpc('tools/call',{'name':'notes_default_folder','arguments':{'source_id':'selected'}})['result']);assert default['folder']['id']=='folder'
                assert rpc('tools/call',{'name':'notes_default_folder','arguments':{'source_id':'foreign'}})['result'].get('isError')
                schema=tool['inputSchema']['properties']['representation'];assert set(schema['enum'])=={'native','markdown'} and schema['default']=='native'
                locator=next(t for t in tools if t['name']=='notes_locate');assert locator['annotations']['readOnlyHint'] is True
                located=data(rpc('tools/call',{'name':'notes_locate','arguments':{'id':'synthetic-note','source_id':'selected'}})['result']);assert located['folders'][0]['id']=='folder' and located['folders'][0]['role']=='unverified'
                assert rpc('tools/call',{'name':'notes_locate','arguments':{'id':'synthetic-note','source_id':'foreign'}})['result'].get('isError')
                search=next(t for t in tools if t['name']=='notes_search');assert search['annotations']['readOnlyHint'] is True
                args_search={'text':'Priority','modified_from':'2026-10-27T12:00:00Z','modified_before':'2026-10-27T13:00:00Z'}
                matched=data(rpc('tools/call',{'name':'notes_search','arguments':args_search})['result']);assert matched['items'][0]['modified']=='2026-10-27T12:30:00.000Z' and matched['items'][0]['match_source']=='exported_table_text'
                expanded=data(rpc('tools/call',{'name':'notes_search','arguments':dict(args_search,text='needle')})['result']);assert 'needle' in expanded['items'][0]['excerpt']
                assert rpc('tools/call',{'name':'notes_search','arguments':dict(args_search,modified_before='2026-10-27T11:00:00Z')})['result'].get('isError')
                titles=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'Title','folder_id':'folder','match_scope':'title'}})['result']);assert titles['items'][0]['title']=='Synthetic Title' and titles['items'][0]['excerpt']==''
                plain=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'needle','folder_id':'folder','match_scope':'plaintext'}})['result']);assert plain['items'][0]['excerpt']=='needle' and plain['match_scope']=='plaintext' and 'not separately searched' in plain['search_coverage']
                recent=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder','match_scope':'title'}})['result']);assert recent['items'][0]['id']=='synthetic-note'
                unlocked=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder','match_scope':'title','locked':False}})['result']);assert unlocked['locked_filter'] is False and unlocked['items'][0]['locked'] is False
                locked_only=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder','match_scope':'title','locked':True}})['result']);assert locked_only['locked_filter'] is True and locked_only['items']==[]
                unshared=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder','match_scope':'title','shared':False}})['result']);assert unshared['shared_filter'] is False and unshared['items'][0]['shared'] is False
                shared_only=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder','match_scope':'title','shared':True}})['result']);assert shared_only['shared_filter'] is True and shared_only['items']==[]
                attached=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder','match_scope':'title','has_attachments':True}})['result']);assert attached['attachment_filter'] is True and attached['items'][0]['has_attachments'] is True
                no_attachment=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder','match_scope':'title','has_attachments':False}})['result']);assert no_attachment['attachment_filter'] is False and no_attachment['items']==[]
                created=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder','match_scope':'title','created_from':'2026-10-27T12:00:00Z','created_before':'2026-10-27T13:00:00Z'}})['result']);assert created['created_filter']['semantics']=='utc_inclusive_from_exclusive_before' and created['items'][0]['created']=='2026-10-27T12:30:00.000Z'
                combined=data(rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder','match_scope':'title','locked':False,'shared':False,'has_attachments':True,'created_from':'2026-10-27T12:00:00Z','modified_before':'2026-10-27T13:00:00Z'}})['result']);assert combined['locked_filter'] is False and combined['shared_filter'] is False and combined['attachment_filter'] is True and combined['items'][0]['created']==combined['items'][0]['modified']
                assert rpc('tools/call',{'name':'notes_search','arguments':{'text':'','folder_id':'folder'}})['result'].get('isError')
                attachments=data(rpc('tools/call',{'name':'notes_attachments','arguments':{'folder_id':'folder','id':'synthetic-note','include_attachment_metadata':True}})['result']);assert attachments['metadata_requested'] is True and attachments['attachments'][0]['native_metadata']['url']=={'status':'unavailable','value':None} and attachments['content_downloaded'] is False
                basic_attachments=data(rpc('tools/call',{'name':'notes_attachments','arguments':{'folder_id':'folder','id':'synthetic-note'}})['result']);assert basic_attachments['source_id']=='selected' and 'native_metadata' not in basic_attachments['attachments'][0]
                args={'folder_id':'folder','id':'synthetic-note'};native=data(call(args));assert 'markdown' not in native['item']
                assert call(dict(args,id='foreign-note')).get('isError')
                locked_native=data(call(dict(args,id='synthetic-locked')))['item'];assert locked_native['locked'] is True and 'html' not in locked_native
                links=data(rpc('tools/call',{'name':'notes_inspect_exported_links','arguments':args})['result']);assert links['links'][0]['target']=='https://example.com/spec' and links['coverage']['targets_opened'] is False
                refs=data(rpc('tools/call',{'name':'notes_find_html_references','arguments':{'folder_id':'folder','target':'https://example.com/spec'}})['result']);assert refs['matches'][0]['id']=='synthetic-note' and refs['coverage']['native_backlinks_complete'] is False
                locked_links=data(rpc('tools/call',{'name':'notes_inspect_exported_links','arguments':dict(args,id='synthetic-locked')})['result']);assert locked_links['links'] is None and locked_links['content_available'] is False
                assert rpc('tools/call',{'name':'notes_inspect_exported_links','arguments':dict(args,expected_modified='different')})['result'].get('isError')
                exported=data(rpc('tools/call',{'name':'notes_export_observation','arguments':args})['result']);assert exported['item']['html']==native['item']['html'] and exported['coverage']['restorable'] is False and exported['coverage']['attachment_content_included'] is False
                locked_export=data(rpc('tools/call',{'name':'notes_export_observation','arguments':dict(args,id='synthetic-locked')})['result']);assert locked_export['item']['locked'] is True and 'html' not in locked_export['item']
                projected=data(call(dict(args,representation='markdown')));item=projected['item'];assert item['html']==native['item']['html'];assert 'Priority' in item['markdown'] and '| ---' in item['markdown'];assert item['markdown_projection']['round_trip_safe'] is False
                text_tool=next(t for t in tools if t['name']=='notes_read_text');assert text_tool['annotations']['readOnlyHint'] is True
                first=data(rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,limit=10)})['result']);assert first['text']=='Synthetic ' and first['next_offset']==10 and first['done'] is False and 'html' not in first and 'request_id' not in first
                rest=data(rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,offset=first['next_offset'],limit=100,expected_modified=first['modified'])})['result']);assert first['text']+rest['text']=='Synthetic text café 🚀' and rest['done'] is True and rest['modified']==first['modified']
                pair=data(rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,offset=20,limit=1)})['result']);assert pair['text']=='🚀' and pair['next_offset']==22
                assert rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,id='oversized-note',limit=2)})['result'].get('isError')
                assert rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,id='extra-ascii-note',limit=2)})['result'].get('isError')
                assert rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,offset=100000)})['result'].get('isError')
                assert rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,limit=True)})['result'].get('isError')
                assert rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,expected_modified='different')})['result'].get('isError')
                assert rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,id='foreign-note')})['result'].get('isError')
                locked=data(rpc('tools/call',{'name':'notes_read_text','arguments':dict(args,id='synthetic-locked')})['result']);assert locked['locked'] is True and locked['content_available'] is False and 'text' not in locked
                assert call(dict(args,representation='execute-script')).get('isError');assert call(dict(args,representation=True)).get('isError')
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
    report={'status':'passed','real_mcp_stdio':True,'synthetic_provider_only':True,'checks':['default_compatibility','enum_schema','markdown_and_loss_report','original_html_retained','read_only_annotations','invalid_representation_refused','no_os_network_or_credentials']};print(json.dumps(report));return report
if __name__=='__main__':run()
