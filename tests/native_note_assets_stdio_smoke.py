"""Real MCP scoped Notes native pin, asset metadata and generated PDF reads with isolated synthetic providers."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from test_native_note_assets import SOURCE,FOLDER,NOTE,ASSET,RAW
from test_native_reminder_files import SOURCE as FILE_SOURCE,ITEM as FILE_ITEM,ATTACHMENT as FILE_ATTACHMENT,RAW as FILE_RAW

def run(policy):
    with tempfile.TemporaryDirectory(prefix='native-once-protocol-') as directory:
        home=Path(directory);audit=home/'audit.json';runtime=home/'runtime'
        env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests'),
            'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off',
            'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':policy,
            'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'1',
            'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'read-only','source_ids':[SOURCE],'metadata_backend':'notes-store-readonly'},'reminders':{'access_mode':'read-only','source_ids':[FILE_SOURCE],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}}),
            'ICLOUD_RUNTIME_ROOT':str(runtime)}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_note_assets_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
            def rpc(method,params):
                nonlocal sequence
                sequence+=1;current=sequence;process.stdin.write((json.dumps({'jsonrpc':'2.0','id':current,'method':method,'params':params})+'\n').encode());process.stdin.flush();deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if select.select([process.stdout],[],[],1)[0]:
                        line=process.stdout.readline()
                        if not line:raise AssertionError('Synthetic server exited')
                        value=json.loads(line)
                        if value.get('id')==current:return value
                raise AssertionError('Synthetic MCP timeout')
            def call(name,args):
                response=rpc('tools/call',{'name':name,'arguments':args});assert 'error' not in response,response;return response['result']
            def data(response):
                assert not response.get('isError'),response
                return response.get('structuredContent') or json.loads(response['content'][0]['text'])
            try:
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-native-once','version':'1'}})
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                args={'folder_id':FOLDER,'id':NOTE}
                metadata=data(call('notes_native_metadata',args));assert metadata['pinned'] is False and metadata['content_read'] is False
                definition=data(call('notes_smart_folder_definition',{'folder_id':FOLDER}));assert definition['definition']=={'synthetic_unknown_filter':True} and definition['semantics_verified'] is False and definition['instructions_trusted'] is False
                assert call('notes_smart_folder_definition',{'folder_id':'foreign'}).get('isError')
                tags=data(call('notes_native_tags',args));assert tags['tags'][0]['native_token']=='SYNTHETIC' and tags['native_tag_inventory_complete'] is False
                assert call('notes_native_tags',dict(args,id='foreign')).get('isError')
                queried=data(call('notes_native_query',{'folder_id':FOLDER,'pinned':False,'tag_token':'SYNTHETIC'}))
                assert queried['items'][0]['id']==NOTE and queried['items'][0]['matched_native_tag_count']==1 and queried['content_returned'] is False
                assert data(call('notes_native_query',{'folder_id':FOLDER,'tag_token':'synthetic'}))['items']==[]
                for bad in ({'folder_id':FOLDER},{'folder_id':'foreign','pinned':True},{'folder_id':FOLDER,'pinned':True,'limit':11}):
                    assert call('notes_native_query',bad).get('isError')
                file_args={'id':FILE_ITEM,'attachment_id':FILE_ATTACHMENT}
                native_file=data(call('reminders_export_attachment',file_args));assert native_file['size']==len(FILE_RAW) and native_file['original_file'] is False and native_file['download_attempted'] is False
                smart=data(call('reminders_smart_list_definition',{'source_id':FILE_SOURCE,'id':'22222222-2222-4222-8222-222222222222'}))
                assert smart['definition']=={'unknown_filter':True} and smart['membership_evaluated'] is False and smart['instructions_trusted'] is False
                assert call('reminders_smart_list_definition',{'source_id':FILE_SOURCE,'id':'builtin'}).get('isError')
                for bad in (dict(file_args,max_bytes=1),dict(file_args,id='foreign'),dict(file_args,path='/tmp/not-allowed')):
                    assert call('reminders_export_attachment',bad).get('isError')
                inventory=data(call('notes_native_assets',args));assert len(inventory['assets'])==1 and inventory['assets'][0]['id']==ASSET and inventory['native_asset_inventory_complete'] is False
                exported=data(call('notes_export_fallback_pdf',dict(args,asset_id=ASSET)));assert exported['size']==len(RAW) and exported['encoding']=='base64' and exported['original_file'] is False and exported['download_attempted'] is False
                for bad in (dict(args,asset_id=ASSET,max_bytes=1),dict(args,asset_id=ASSET,max_bytes=1048577),dict(args,asset_id=ASSET,id='foreign'),dict(args,asset_id=ASSET,output_path='/tmp/not-allowed')):
                    assert call('notes_export_fallback_pdf',bad).get('isError')
                assert call('notes_native_metadata',dict(args,folder_id='foreign')).get('isError')
                assert data(call('notes_access_status',{}))['effective_access']=='read-only'
                assert data(call('reminders_access_status',{}))['effective_access']=='read-only'

            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_providers_only':True,'policy':policy,'services':['notes','reminders'],'settings_remained_read_only':True,'native_metadata_and_attachment_scope_and_bounds_verified':True,'network_helper_attempts':[]}

if __name__=='__main__':
    results=[run(policy) for policy in ('trusted-client','local-receipt')];path=ROOT/'build/reports/NATIVE-NOTE-ASSETS-STDIO.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
