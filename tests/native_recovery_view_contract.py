"""Production recovery-view lookup against synthetic Objective-C providers."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
static NSString *scenario=@"present";
static NSString *source=@"11111111-1111-1111-1111-111111111111";
static NSString *target=@"22222222-2222-2222-2222-222222222222";
@interface FakeID:NSObject
@property NSUUID *uuid; @property NSString *entityName;
@end
@implementation FakeID @end
@interface FakeCaps:NSObject
-(BOOL)supportsRecentlyDeletedList;
@end
@implementation FakeCaps
-(BOOL)supportsRecentlyDeletedList{return ![scenario isEqual:@"unsupported"];}
@end
@interface FakeAccount:NSObject
@property FakeID *remObjectID; @property FakeCaps *capabilities;
@end
@implementation FakeAccount @end
@interface FakeInvocation:NSObject
-(id)initWithFetchResultTokenToDiffAgainst:(id)value;
@end
@implementation FakeInvocation
-(id)initWithFetchResultTokenToDiffAgainst:(id)value{return [super init];}
@end
@interface FakeResult:NSObject
@property NSData *resultData;
@end
@implementation FakeResult @end
@interface FakeStore:NSObject
-(id)fetchAccountsWithError:(NSError **)error;
-(id)resultFromPerformingSwiftInvocation:(id)inv parametersData:(NSData *)data storages:(id)stores error:(NSError **)error;
@end
@implementation FakeStore
-(id)fetchAccountsWithError:(NSError **)error{
 FakeAccount *a=[FakeAccount new];a.remObjectID=[FakeID new];a.remObjectID.uuid=[[NSUUID alloc]initWithUUIDString:source];a.remObjectID.entityName=@"REMCDAccount";a.capabilities=[FakeCaps new];
 if([scenario isEqual:@"foreign"])a.remObjectID.uuid=[[NSUUID alloc]initWithUUIDString:@"33333333-3333-3333-3333-333333333333"];
 return [scenario isEqual:@"duplicate_account"]?@[a,a]:@[a];
}
-(id)resultFromPerformingSwiftInvocation:(id)inv parametersData:(NSData *)data storages:(id)stores error:(NSError **)error{
 if([scenario isEqual:@"error"]){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:nil];return nil;}
 NSDictionary *obj=@{@"objectID":@{@"uuid":target,@"entityName":@"REMCDReminder"}};
 id list=[scenario isEqual:@"absent"]?@[]:[scenario isEqual:@"duplicate"]?@[obj,obj]:[scenario isEqual:@"malformed"]?@[@{@"objectID":@{}}]:@[obj];
 FakeResult *result=[FakeResult new];result.resultData=[NSPropertyListSerialization dataWithPropertyList:@{@"model":@{@"reminders":list}} format:NSPropertyListBinaryFormat_v1_0 options:0 error:NULL];return result;
}
@end
int main(int argc,char **argv){@autoreleasepool{
 scenario=[NSString stringWithUTF8String:argv[1]];
 NSData *input=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"id":target} options:0 error:NULL];
 NSData *output=ICloudRemindersRecoveryLookup(input);fwrite(output.bytes,1,output.length,stdout);return 0;
}}
'''
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text()
    a=source.index('static BOOL FlagReadInterface(void){');b=source.index('static id ObjectValue(',a)
    source=source[:a]+'static BOOL FlagReadInterface(void){return YES;}\n'+source[b:]
    source=source.replace('!FlagReadInterface() || !dlopen("/System/Library/PrivateFrameworks/ReminderKitInternal.framework/ReminderKitInternal",RTLD_LAZY|RTLD_LOCAL)','!FlagReadInterface()')
    for actual,fake in [('REMStore','FakeStore'),('REMRemindersListDataView_RecentlyDeletedInvocation','FakeInvocation')]:source=source.replace('NSClassFromString(@"'+actual+'")','NSClassFromString(@"'+fake+'")')
    with tempfile.TemporaryDirectory(prefix='recovery-view-contract-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(source+'\n'+FIXTURE)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        def call(s):return json.loads(subprocess.run([str(p/'contract'),s],capture_output=True,text=True,check=True,timeout=5).stdout)
        yes=call('present');assert yes['found_as_root'] is True and yes['data_mutated'] is False and yes['view']=='provider_recently_deleted'
        assert call('absent')['found_as_root'] is False
        for scenario in ('foreign','duplicate_account','unsupported','error','duplicate','malformed'):assert call(scenario)['error']=='private_metadata_unavailable',scenario
    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['fixed_scoped_provider_view','true_false_boolean','foreign_duplicate_denied_malformed_refused','no_mutation']}))
if __name__=='__main__':run()
