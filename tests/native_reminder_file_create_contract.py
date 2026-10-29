"""Exercise the shipped writer, including the gate before SDK file side effects."""
import json,subprocess,tempfile
from pathlib import Path
from native_grocery_mode_contract import FIXTURE as BASE,ROOT
FILE=r'''
static int constructors=0,artifacts=0;
@interface FFile:NSObject
@property FID *remObjectID;@property FID *accountID;@property FID *reminderID;@property NSString *uti;
-(id)initWithObjectID:(id)object accountID:(id)account reminderID:(id)reminder UTI:(id)uti fileSize:(unsigned long long)size fileURL:(id)url data:(id)data;
@end
@implementation FFile
-(id)initWithObjectID:(id)object accountID:(id)account reminderID:(id)parent UTI:(id)uti fileSize:(unsigned long long)size fileURL:(id)url data:(id)data{constructors++;if([mode isEqual:@"constructor_exception"])[NSException raise:@"synthetic"format:@"synthetic"];self=[super init];self.remObjectID=object;self.accountID=account;self.reminderID=parent;self.uti=uti;if([mode isEqual:@"wrong_file_parent"])self.reminderID=account;if([mode isEqual:@"wrong_file_account"])self.accountID=parent;if([mode isEqual:@"wrong_file_id"])self.remObjectID=parent;return self;}
@end
'''
MAIN=r'''
int main(int argc,const char**argv){@autoreleasepool{
mode=[NSString stringWithUTF8String:argv[1]];NSString *source=@"11111111-1111-4111-8111-111111111111",*listing=@"22222222-2222-4222-8222-222222222222",*task=@"33333333-3333-4333-8333-333333333333",*attachment=@"44444444-4444-4444-8444-444444444444";
account=[FObject new];account.remObjectID=ident(@"REMCDAccount",source);account.storage=[FStorage new];account.storage.remObjectID=account.remObjectID;list=[FObject new];list.remObjectID=ident(@"REMCDList",listing);list.storage=[FStorage new];list.storage.remObjectID=list.remObjectID;list.storage.accountID=account.remObjectID;
reminder=[FObject new];reminder.remObjectID=ident(@"REMCDReminder",task);reminder.list=list;reminder.account=account;list.account=account;reminder.storage=[FStorage new];reminder.storage.remObjectID=reminder.remObjectID;reminder.storage.accountID=account.remObjectID;reminder.storage.attachments=@[];
graph=@{@"source_id":source,@"list_id":listing,@"data_mutated":@NO,@"nodes":@[@{@"id":task,@"parent_id":NSNull.null}],@"ordered_ids":@[task]};NSDictionary *reviewedGraph=@{@"nodes":@[@{@"id":task,@"parent_id":NSNull.null,@"completed":@NO}],@"ordered_ids":@[task]};sections=@{@"sections":@[]};NSDictionary *inventory=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadAttachments(nil)options:0 error:NULL];
NSMutableDictionary *request=[@{@"source_id":source,@"list_id":listing,@"id":task,@"attachment_id":attachment,@"uti":@"public.plain-text",@"data_base64":@"eA==",@"sha256":@"2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",@"before":@{@"role":features(),@"graph":reviewedGraph,@"sections":sections,@"items":@[@{@"item":@{@"id":task},@"attachments":inventory[@"native_metadata"]}]}}mutableCopy];
if([mode isEqual:@"graph_drift"])graph=@{@"nodes":@[]};if([mode isEqual:@"section_drift"])sections=@{@"sections":@[@{}]};if([mode isEqual:@"extra_field"])request[@"selector"]=@"never";if([mode isEqual:@"bad_hash"])request[@"sha256"]=@"0";if([mode isEqual:@"bad_base64"])request[@"data_base64"]=@"eB==";if([mode isEqual:@"bad_type"])request[@"uti"]=@"image/png";
id response=[NSJSONSerialization JSONObjectWithData:ICloudRemindersAddTextFile(EncodeFlag(request),^BOOL{gates++;return ![mode isEqual:@"closed"];})options:0 error:NULL];NSData *output=EncodeFlag(@{@"response":response,@"gate_calls":@(gates),@"save_calls":@(saves),@"constructors":@(constructors),@"temporary_side_effects":@(artifacts)});fwrite(output.bytes,1,output.length,stdout);
}}
'''
def fixture():
 s=BASE.replace('@property NSString *entityName;@property NSUUID *uuid;','@property NSString *entityName;@property NSUUID *uuid;+(id)objectIDWithURL:(NSURL*)url;').replace('@implementation FID','@implementation FID\n+(id)objectIDWithURL:(NSURL*)url{return ident(url.host,url.lastPathComponent);}',1)
 # The factory uses the ordinary identity constructor defined below it.
 s=s.replace('@interface FID:', '@class FID;static FID *ident(NSString*,NSString*);\n@interface FID:',1)
 s=s.replace('@property NSString *name;','@property NSArray *attachments;@property NSString *name;',1).replace('v.name=self.name;','v.name=self.name;v.attachments=self.attachments;',1).replace('v.name==self.name &&','v.name==self.name && (v.attachments==self.attachments || [v.attachments isEqual:self.attachments]) &&',1)
 s=s.replace('@property FID *remObjectID;@property FStorage *storage;','@property FID *remObjectID;@property FStorage *storage;@property id list;@property id account;@property id parentReminder;',1)
 s=s.replace('@class FCtx;',FILE+'\n@class FCtx;')
 s=s.replace('-(id)groceryContextChangeItem;','-(id)attachmentContext;-(id)groceryContextChangeItem;',1)
 s=s.replace('-(void)setShouldCategorizeGroceryItems:(BOOL)value;','-(void)addAttachment:(id)value;-(void)setShouldCategorizeGroceryItems:(BOOL)value;',1)
 s=s.replace('@implementation FCtx',r'''
@implementation FCtx
-(void)addAttachment:(id)value{artifacts++;self.change.storage.attachments=@[value];self.change.changedKeys=[NSMutableSet setWithObject:@"attachments"];if([mode isEqual:@"extra_reminder_key"])[self.change.changedKeys addObject:@"name"];if([mode isEqual:@"silent_reminder"])self.change.storage.name=@"Unreviewed";}
''',1)
 s=s.replace('@implementation FChange','@implementation FChange\n-(id)attachmentContext{FCtx *v=[FCtx new];v.change=self;return v;}',1)
 s=s.replace('static FObject *account,*list;','static FObject *account,*list,*reminder;')
 s=s.replace('@property FChange *change;@property FChange *ac;','@property FChange *change;@property FChange *ac;@property FChange *rc;-(id)updateReminder:(id)value;',1)
 a=s.index('-(id)trackedListChangeItems{');s=s[:a]+r'''
-(id)updateReminder:(FObject*)value{[self updateList:value.list];self.rc=[FChange new];self.rc.storage=[value.storage copy];self.rc.changedKeys=[NSMutableSet set];if([mode isEqual:@"silent_list"])self.change.storage.name=@"Unreviewed";return self.rc;}
'''+s[a:]
 s=s.replace('-(id)trackedReminderChangeItems{return [mode isEqual:@"unrelated_reminder"]?@{@"unreviewed":@1}:@{};}',r'''-(id)trackedReminderChangeItems{FWrap *w=[FWrap new];w.valueWithoutPerformingCopy=self.rc;return [mode isEqual:@"unrelated_reminder"]?@{@"unreviewed":w}:@{reminder.remObjectID:w};}''')
 s+='''
static NSData *ICloudRemindersListOrder(NSData *data){return EncodeFlag(graph);}
static NSData *ICloudRemindersReadAttachments(NSData *data){return EncodeFlag(@{@"native_metadata":@{@"attachments":reminder.storage.attachments?:@[]}});}
static id SelectedReminderWithStore(NSData *data,NSDictionary **failure,id __strong *store){*store=[NSObject new];return reminder;}
static BOOL LifecycleListPrivate(id object,NSString *source,NSString *listing){return object==list && [source isEqual:account.remObjectID.uuid.UUIDString] && [listing isEqual:list.remObjectID.uuid.UUIDString];}
'''
 return s

def run():
 source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();prefix=source[:source.index('static BOOL FlagReadInterface(void)')].replace('#import "reminders-model.h"','#import <Foundation/Foundation.h>');support=source[source.index('static id ObjectValue'):source.index('static id SelectedFailure')];tracked=source[source.index('static BOOL TrackedGroupObject'):source.index('NSData *ICloudRemindersCreateRootGroup')];list_tracker=source[source.index('static BOOL LifecycleTrackedList'):source.index('static BOOL LifecycleListPrivate')];graph_support=source[source.index('static BOOL LifecycleGraphMatches'):source.index('static BOOL LifecycleTrackedList')];start=source.index('NSData *ICloudRemindersAddTextFile(');writer=source[start:source.index('\n}\n',start)+3]
 for real,fake in(('REMSaveRequest','FSave'),('REMObjectID','FID'),('REMFileAttachment','FFile'),('REMReminderAttachmentContextChangeItem','FCtx')):writer=writer.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
 with tempfile.TemporaryDirectory(prefix='native-text-file-')as directory:
  p=Path(directory);(p/'test.m').write_text(prefix+support+fixture()+tracked+list_tracker+graph_support+writer+MAIN);r=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(p/'test.m'),'-o',str(p/'test')],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr
  def call(mode):return json.loads(subprocess.run([str(p/'test'),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
  value=call('valid');assert value['gate_calls']==value['save_calls']==value['constructors']==value['temporary_side_effects']==1,value;assert value['response']['result']['saved_locally']is True
  value=call('closed');assert value['gate_calls']==1 and value['save_calls']==value['constructors']==value['temporary_side_effects']==0,value
  for mode in('graph_drift','section_drift','extra_field','bad_hash','bad_base64','bad_type','alias_copy','shared','shopping','group'):
   value=call(mode);assert value['response']['error']and value['gate_calls']==value['save_calls']==value['constructors']==value['temporary_side_effects']==0,(mode,value)
  for mode in('wrong_file_parent','wrong_file_account','wrong_file_id','constructor_exception','extra_reminder_key','silent_reminder','silent_list','account_key','account_content','wrong_wrapper','unrelated_reminder','save_error'):
   value=call(mode);assert value['response']['error']and value['gate_calls']==value['constructors']==1 and value['save_calls']==(1 if mode=='save_error'else 0),(mode,value)
   # Swift's shared gate also records dispatch for any returned post-gate error.
   assert value['response']['mutation_dispatched']is True,(mode,value)
 return {'status':'passed','native_store_access':False,'gate_precedes_constructor_and_temporary_file_side_effects':True,'post_gate_guard_failure_retains_uncertainty':True,'attachment_only_full_storage_and_exact_trackers_verified':True}
if __name__=='__main__':print(json.dumps(run()))
