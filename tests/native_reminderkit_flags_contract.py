"""Shipped fixed ReminderKit read against synthetic objects, without Apple stores."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
@interface FixtureID:NSObject
@property NSUUID *uuid;
@property NSString *entityName;
+(id)objectIDWithURL:(NSURL *)url;
@end
@implementation FixtureID
+(id)objectIDWithURL:(NSURL *)url{FixtureID *v=[FixtureID new];v.uuid=[[NSUUID alloc]initWithUUIDString:url.lastPathComponent];v.entityName=url.host;return v;}
@end
@interface FixtureAccount:NSObject
@property FixtureID *remObjectID;
@end
@implementation FixtureAccount
@end
@interface FixtureList:NSObject
@property FixtureID *remObjectID;
@property FixtureAccount *account;
@end
@implementation FixtureList
@end
@interface FixtureFlag:NSObject
@property NSInteger flagged;
@end
@implementation FixtureFlag
@end
@interface FixtureReminder:NSObject
@property FixtureID *remObjectID;
@property FixtureAccount *account;
@property FixtureList *list;
@property id flaggedContext;
@end
@implementation FixtureReminder
@end
static FixtureReminder *fixture;
static BOOL denied=NO;
@interface FixtureStore:NSObject
-(id)fetchReminderWithObjectID:(id)objectID error:(NSError **)error;
@end
@implementation FixtureStore
-(id)fetchReminderWithObjectID:(id)objectID error:(NSError **)error{
 if(denied){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:@{NSLocalizedDescriptionKey:@"private-content-must-not-leak"}];return nil;}
 NSCAssert([[objectID entityName] isEqual:@"REMCDReminder"],@"native Core Data entity required for lookup");
 return [[objectID uuid] isEqual:fixture.remObjectID.uuid]?fixture:nil;
}
@end
static FixtureID *Identity(NSString *entity,NSString *value){FixtureID *v=[FixtureID new];v.entityName=entity;v.uuid=[[NSUUID alloc]initWithUUIDString:value];return v;}
static NSDictionary *Call(NSDictionary *request){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadFlag(data) options:0 error:NULL];}
int main(){@autoreleasepool{
 NSString *task=@"11111111-1111-4111-8111-111111111111",*list=@"22222222-2222-4222-8222-222222222222",*source=@"33333333-3333-4333-8333-333333333333",*other=@"44444444-4444-4444-8444-444444444444";
 fixture=[FixtureReminder new];fixture.remObjectID=Identity(@"REMCDReminder",task);fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);fixture.list=[FixtureList new];fixture.list.remObjectID=Identity(@"REMCDList",list);fixture.list.account=fixture.account;FixtureFlag *flag=[FixtureFlag new];fixture.flaggedContext=flag;
 NSDictionary *request=@{@"id":task,@"source_id":source,@"list_id":list};
 NSDictionary *first=Call(request);NSCAssert(first[@"native_metadata"] && ![first[@"native_metadata"][@"flagged"] boolValue],@"unflagged actual integer metadata");
 NSCAssert(CFGetTypeID((__bridge CFTypeRef)first[@"native_metadata"][@"flagged"])==CFBooleanGetTypeID(),@"JSON boolean not integer");
 flag.flagged=1;NSDictionary *second=Call(request);NSCAssert([second[@"native_metadata"][@"flagged"] boolValue] && ![second[@"native_metadata"][@"native_metadata_complete"] boolValue],@"flagged and bounded coverage");
 fixture.remObjectID.entityName=@"REMReminder";NSCAssert([Call(request)[@"category"] isEqual:@"native_identity_mismatch"],@"native entity names bound as well as UUIDs");fixture.remObjectID.entityName=@"REMCDReminder";
 flag.flagged=2;NSCAssert([Call(request)[@"category"] isEqual:@"unknown_flag_state"],@"unknown never false");flag.flagged=0;
 fixture.flaggedContext=nil;NSCAssert([Call(request)[@"category"] isEqual:@"unsupported_flag_metadata"],@"nil never false");fixture.flaggedContext=flag;
 for(NSString *key in @[@"id",@"source_id",@"list_id"]){NSMutableDictionary *changed=[request mutableCopy];changed[key]=other;NSCAssert(Call(changed)[@"error"],@"scope refusal");}
 FixtureAccount *foreign=[FixtureAccount new];foreign.remObjectID=Identity(@"REMCDAccount",other);fixture.list.account=foreign;NSCAssert([Call(request)[@"category"] isEqual:@"native_identity_mismatch"],@"account relation bound");fixture.list.account=fixture.account;
 denied=YES;NSDictionary *failure=Call(request);NSCAssert([failure[@"category"] isEqual:@"private_read_denied_or_unavailable"] && [failure[@"native_error_domain"] isEqual:@"other"] && ![[failure description] containsString:@"private-content"],@"denial not empty and private diagnostics redacted");denied=NO;
 NSCAssert(Call(@{@"id":@YES,@"source_id":source,@"list_id":list})[@"error"],@"bad types");
 NSCAssert(Call(@{@"id":task,@"source_id":source,@"list_id":list,@"selector":@"anything"})[@"error"],@"no arbitrary selectors");
 puts("fixed native flag read: scope, types, denied/missing/unknown refusal passed");return 0;
}}
'''
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();source=source.replace('    if(NSProcessInfo.processInfo.operatingSystemVersion.majorVersion!=27)return NO;','').replace('    if(!dlopen("/System/Library/PrivateFrameworks/ReminderKit.framework/ReminderKit",RTLD_LAZY|RTLD_LOCAL))return NO;','')
    for real,fake in [('REMStore','FixtureStore'),('REMObjectID','FixtureID'),('REMReminder','FixtureReminder')]:source=source.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
    with tempfile.TemporaryDirectory(prefix='native-flag-contract-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(source+'\n'+FIXTURE)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['actual_fixed_read_and_runtime_signatures','true_and_false_integer_flag','exact_task_list_source_and_account_relation','nil_unknown_or_denied_not_empty_success','no_arbitrary_selectors_or_save_api']}))
if __name__=='__main__':run()
