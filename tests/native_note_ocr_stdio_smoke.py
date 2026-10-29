"""Actual MCP OCR schema/provenance/scope tests with synthetic providers only."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run(policy):
 with tempfile.TemporaryDirectory(prefix='native-ocr-stdio-')as directory:
  home=Path(directory);audit=home/'audit.json';env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests'),'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':policy,'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'0','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'read-only','source_ids':['selected'],'helper_path':'/synthetic/helper'}}),'ICLOUD_RUNTIME_ROOT':str(home/'runtime')}
  with (home/'stderr.log').open('wb')as stderr:
   process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_note_ocr_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
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
   def call(args):
    value=rpc('tools/call',{'name':'notes_ocr_image_attachment','arguments':args});assert 'error'not in value,value;return value['result']
   def data(value):
    assert not value.get('isError'),value
    return value.get('structuredContent')or json.loads(value['content'][0]['text'])
   try:
    rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-ocr','version':'1'}});process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush();tool=next(v for v in rpc('tools/list',{})['result']['tools']if v['name']=='notes_ocr_image_attachment');assert tool['annotations']['readOnlyHint']and not tool['annotations']['destructiveHint'];assert 'data'not in tool['inputSchema']['properties']and 'path'not in tool['inputSchema']['properties'];args={'folder_id':'folder','id':'note','attachment_id':'image'};value=data(call(args));assert value['lines'][0]['text']=='SYNTHETIC 123'and value['derived']and not value['native_notes_ocr']and not value['data_mutated']and not value['complete_backup'];assert 'data'not in value
    for fields in ({'folder_id':'foreign'},{'id':'foreign'},{'attachment_id':'foreign'},{'max_bytes':0},{'max_bytes':131073}):assert call(dict(args,**fields)).get('isError')
    assert data(rpc('tools/call',{'name':'notes_access_status','arguments':{}})['result'])['effective_access']=='read-only'
   finally:
    process.stdin.close()
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
    process.stdout.close()
  assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
  return {'status':'passed','policy':policy,'transport':'real stdio JSON-RPC','synthetic_providers_only':True,'derived_native_distinction_and_no_raw_bytes_returned':True,'scope_and_size_refusals':True,'network_helper_attempts':[]}
if __name__=='__main__':
 results=[run(policy)for policy in ('trusted-client','local-receipt')];out=ROOT/'build/reports/NATIVE-NOTE-OCR-STDIO.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
