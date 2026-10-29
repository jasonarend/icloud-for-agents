"""Redacted native failure metadata across the C/Swift bridge; no Apple stores."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run():
 c=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();a=c.index('static NSDictionary *MutationFailure(');helper=c[a:c.index('static NSString *Signature(',a)]
 main=r'''
int main(int argc,char**argv){@autoreleasepool{NSString *domain=argc>1?[NSString stringWithUTF8String:argv[1]]:nil;NSError *error=domain?[NSError errorWithDomain:domain code:-3004 userInfo:@{NSLocalizedDescriptionKey:@"Private synthetic account and contents",@"secret":@"Never retain"}]:nil;NSData *out=[NSJSONSerialization dataWithJSONObject:MutationFailure(error)options:0 error:NULL];fwrite(out.bytes,1,out.length,stdout);}}
'''
 swift=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();a=swift.index('func failPrivateReminder(');bridge=swift[a:swift.index('func requestData()',a)]
 stub='''import Foundation
import CoreFoundation
import Darwin
let didDispatch=CommandLine.arguments[1]=="true"
class NativeMutationGate {static func cancel()->Bool {return didDispatch}}
func respond(_ value:[String:Any])->Never {FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject:value));exit(0)}
'''
 with tempfile.TemporaryDirectory(prefix='native-mutation-diagnostic-')as directory:
  root=Path(directory);(root/'main.m').write_text('#import <Foundation/Foundation.h>\n'+helper+main);(root/'main.swift').write_text(stub+bridge+'\nlet response=try! JSONSerialization.jsonObject(with:FileHandle.standardInput.readDataToEndOfFile())as! [String:Any]\nfailPrivateReminder(response)\n')
  for compiler,source,binary,args in [('/usr/bin/clang','main.m','c',['-fobjc-arc','-framework','Foundation']),('/usr/bin/swiftc','main.swift','swift',[])]:
   result=subprocess.run([compiler,*args,str(root/source),'-o',str(root/binary)],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
  for domain,expected in [(None,None),('com.apple.reminderkit','com.apple.reminderkit'),('NSCocoaErrorDomain','NSCocoaErrorDomain'),('private@example.com','other')]:
   response=json.loads(subprocess.run([str(root/'c'),*([domain]if domain else[])],capture_output=True,text=True,check=True,timeout=5).stdout);assert response['mutation_dispatched']is True and response['error']=='mutation_failed_or_uncertain'
   if expected:assert response['native_error_domain']==expected and response['native_error_code']==-3004
   else:assert 'native_error_domain'not in response
   assert 'Private synthetic'not in json.dumps(response)and'Never retain'not in json.dumps(response)
  def call(value,dispatched=True):return json.loads(subprocess.run([str(root/'swift'),'true'if dispatched else'false'],input=json.dumps(value),capture_output=True,text=True,check=True,timeout=5).stdout)
  valid={'native_error_domain':'com.apple.reminderkit','native_error_code':-3004,'description':'Private synthetic contents','mutation_dispatched':False}
  response=call(valid);assert response=={'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True,'native_error_domain':'com.apple.reminderkit','native_error_code':-3004}
  response=call(valid,False);assert response['error']=='unsupported_edit_target'and response['mutation_dispatched']is False
  for value in ({'native_error_domain':'private@example.com','native_error_code':1},{'native_error_domain':'other','native_error_code':True},{'native_error_domain':'other','native_error_code':1.5},{'native_error_domain':'other'}):
   response=call(value);assert set(response)=={'protocol_version','error','mutation_dispatched'}
 return {'status':'passed','apple_store_opened':False,'redacted_sdk_domain_code_only':True,'unknown_domains_localized_descriptions_and_user_info_not_disclosed':True,'actual_gate_state_preserved':True}
if __name__=='__main__':print(json.dumps(run()))
