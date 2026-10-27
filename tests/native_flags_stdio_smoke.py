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
                assert call(dict(args,include_native_hierarchy='invalid')).get('isError')
                query=next(t for t in tools if t['name']=='reminders_query');assert query['annotations']['readOnlyHint'] is True
                flag_query=data(rpc('tools/call',{'name':'reminders_query','arguments':{'flagged':True,'limit':1}})['result']);assert flag_query['items']==[] and flag_query['done'] is False and flag_query['next_offset']==1
                assert rpc('tools/call',{'name':'reminders_query','arguments':{'flagged':'invalid'}})['result'].get('isError')
                daily=data(rpc('tools/call',{'name':'reminders_daily','arguments':{'date':'2026-10-27','timezone':'UTC','flagged':True,'limit':1,'list_id':'list'}})['result']);assert daily['items']==[] and daily['done'] is False and daily['next_offset']==1

            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
    report={'status':'passed','real_mcp_stdio':True,'synthetic_provider_only':True,'checks':['default_compatibility','boolean_schema','optional_flag_and_tag_metadata','public_metadata_retained','read_only_annotations','invalid_selector_refused','no_os_network_or_credentials']};print(json.dumps(report));return report
if __name__=='__main__':run()
