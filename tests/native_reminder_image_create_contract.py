"""Run the actual PNG writer with adversarial in-memory SDK objects."""
import base64,hashlib,json,subprocess,sys,tempfile
from pathlib import Path
from native_reminder_file_create_contract import fixture as file_fixture,MAIN as FILE_MAIN,ROOT
sys.path.insert(0,str(ROOT/'src'))
from test_png_format import png

def fixture():
 s=file_fixture().replace('@property NSString *uti;','@property NSString *uti;@property unsigned long long width;@property unsigned long long height;',1)
 s=s.replace('fileURL:(id)url data:(id)data','fileURL:(id)url data:(id)data width:(unsigned long long)width height:(unsigned long long)height')
 s=s.replace('self.uti=uti;','self.uti=uti;self.width=width;self.height=height;if([mode isEqual:@"wrong_dimensions"])self.width++;',1)
 return s

def main():
 data=png();s=FILE_MAIN.replace('ICloudRemindersAddTextFile','ICloudRemindersAddPNGImage').replace('@"uti":@"public.plain-text"','@"uti":@"public.png"').replace('@"data_base64":@"eA=="','@"data_base64":@"'+base64.b64encode(data).decode()+'",@"width":@2,@"height":@2').replace('2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881',hashlib.sha256(data).hexdigest())
 s=s.replace('id response=[NSJSONSerialization',r'''if([mode isEqual:@"boolean_width"])request[@"width"]=@YES;if([mode isEqual:@"zero_height"])request[@"height"]=@0;if([mode isEqual:@"header_dimension"])request[@"width"]=@3;
id response=[NSJSONSerialization''')
 return s

def run():
 source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();prefix=source[:source.index('static BOOL FlagReadInterface(void)')].replace('#import "reminders-model.h"','#import <Foundation/Foundation.h>');support=source[source.index('static id ObjectValue'):source.index('static id SelectedFailure')];tracked=source[source.index('static BOOL TrackedGroupObject'):source.index('NSData *ICloudRemindersCreateRootGroup')];list_tracker=source[source.index('static BOOL LifecycleTrackedList'):source.index('static BOOL LifecycleListPrivate')];graph_support=source[source.index('static BOOL LifecycleGraphMatches'):source.index('static BOOL LifecycleTrackedList')];start=source.index('NSData *ICloudRemindersAddPNGImage(');writer=source[start:source.index('\n}\n',start)+3]
 for real,fake in(('REMSaveRequest','FSave'),('REMObjectID','FID'),('REMImageAttachment','FFile'),('REMReminderAttachmentContextChangeItem','FCtx')):writer=writer.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
 with tempfile.TemporaryDirectory(prefix='native-png-image-')as directory:
  p=Path(directory);(p/'test.m').write_text(prefix+support+fixture()+tracked+list_tracker+graph_support+writer+main());r=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(p/'test.m'),'-o',str(p/'test')],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr
  def call(mode):return json.loads(subprocess.run([str(p/'test'),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
  value=call('valid');assert value['gate_calls']==value['save_calls']==value['constructors']==value['temporary_side_effects']==1,value;assert value['response']['result']['saved_locally']is True
  value=call('closed');assert value['gate_calls']==1 and value['save_calls']==value['constructors']==value['temporary_side_effects']==0,value
  for mode in('graph_drift','section_drift','extra_field','bad_hash','bad_base64','bad_type','alias_copy','shared','shopping','group','boolean_width','zero_height','header_dimension'):
   value=call(mode);assert value['response']['error']and value['gate_calls']==value['save_calls']==value['constructors']==value['temporary_side_effects']==0,(mode,value)
  for mode in('wrong_file_parent','wrong_file_account','wrong_file_id','wrong_dimensions','constructor_exception','extra_reminder_key','silent_reminder','silent_list','account_key','account_content','wrong_wrapper','unrelated_reminder','save_error'):
   value=call(mode);assert value['response']['error']and value['gate_calls']==value['constructors']==1 and value['save_calls']==(1 if mode=='save_error'else 0),(mode,value);assert value['response']['mutation_dispatched']is True,(mode,value)
 return {'status':'passed','native_store_access':False,'gate_precedes_constructor_and_temp_side_effects':True,'exact_typed_image_dimensions_and_parent_account_identity':True,'post_gate_failure_retains_uncertainty':True,'attachment_only_storage_and_trackers_verified':True}
if __name__=='__main__':print(json.dumps(run()))
