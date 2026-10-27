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
+(id)objectIDWithURL:(NSURL *)url;
@end
@implementation FakeID
+(id)objectIDWithURL:(NSURL *)url{FakeID *v=[FakeID new];v.uuid=[[NSUUID alloc]initWithUUIDString:url.lastPathComponent];v.entityName=url.host;return v;}
@end
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
@interface FakeReminder:NSObject
@property FakeID *remObjectID; @property FakeAccount *account;
-(id)titleAsString;
@end
@implementation FakeReminder
-(id)titleAsString{return [scenario isEqual:@"missing_title"]?nil:@"Synthetic recovery title";}
@end
@interface FakeStore:NSObject
-(id)fetchAccountsWithError:(NSError **)error;
-(id)fetchReminderIncludingMarkedForDeleteWithObjectID:(id)identity error:(NSError **)error;
-(id)resultFromPerformingSwiftInvocation:(id)inv parametersData:(NSData *)data storages:(id)stores error:(NSError **)error;
@end
@implementation FakeStore
-(id)fetchReminderIncludingMarkedForDeleteWithObjectID:(id)identity error:(NSError **)error{
 FakeReminder *r=[FakeReminder new];r.remObjectID=identity;r.account=[FakeAccount new];r.account.remObjectID=[FakeID new];r.account.remObjectID.uuid=[[NSUUID alloc]initWithUUIDString:[scenario isEqual:@"foreign_reminder"]?@"33333333-3333-3333-3333-333333333333":source];r.account.remObjectID.entityName=@"REMCDAccount";return r;
}
-(id)fetchAccountsWithError:(NSError **)error{
 FakeAccount *a=[FakeAccount new];a.remObjectID=[FakeID new];a.remObjectID.uuid=[[NSUUID alloc]initWithUUIDString:source];a.remObjectID.entityName=@"REMCDAccount";a.capabilities=[FakeCaps new];
 if([scenario isEqual:@"foreign"])a.remObjectID.uuid=[[NSUUID alloc]initWithUUIDString:@"33333333-3333-3333-3333-333333333333"];
 return [scenario isEqual:@"duplicate_account"]?@[a,a]:@[a];
}
-(id)resultFromPerformingSwiftInvocation:(id)inv parametersData:(NSData *)data storages:(id)stores error:(NSError **)error{
 if([scenario isEqual:@"error"]){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:nil];return nil;}
 NSDictionary *child=@{@"objectID":@{@"uuid":@"44444444-4444-4444-4444-444444444444",@"entityName":@"REMCDReminder"}};
 id grandchildren=[scenario isEqual:@"cycle"]?@[@{@"objectID":@{@"uuid":target,@"entityName":@"REMCDReminder"}}]:@[];
 child=@{@"objectID":child[@"objectID"],@"subtasks":grandchildren};
 NSMutableArray *tooMany=[NSMutableArray array];for(int i=0;i<101;i++)[tooMany addObject:child];
 id children=[scenario isEqual:@"bad_children"]?@[child,child]:[scenario isEqual:@"too_many"]?tooMany:@[child];
 NSDictionary *obj=@{@"objectID":@{@"uuid":target,@"entityName":@"REMCDReminder"},@"subtasks":children};
 id list=[scenario isEqual:@"absent"]?@[]:[scenario isEqual:@"duplicate"]?@[obj,obj]:[scenario isEqual:@"malformed"]?@[@{@"objectID":@{}}]:@[obj];
 FakeResult *result=[FakeResult new];result.resultData=[NSPropertyListSerialization dataWithPropertyList:@{@"model":@{@"reminders":list}} format:NSPropertyListBinaryFormat_v1_0 options:0 error:NULL];return result;
}
@end
int main(int argc,char **argv){@autoreleasepool{
 scenario=[NSString stringWithUTF8String:argv[1]];BOOL tree=[scenario hasPrefix:@"tree_"],list=[scenario hasPrefix:@"list_"];if(tree||list)scenario=[scenario substringFromIndex:5];
 NSString *requested=[scenario isEqual:@"child"]?@"44444444-4444-4444-4444-444444444444":target;
 NSData *input=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"id":requested} options:0 error:NULL];
 if(list)input=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"limit":@20,@"offset":[scenario isEqual:@"out_of_range"]?@5000:@0} options:0 error:NULL];
 NSData *output=list?ICloudRemindersRecoveryList(input):tree?ICloudRemindersRecoveryTree(input):ICloudRemindersRecoveryLookup(input);fwrite(output.bytes,1,output.length,stdout);return 0;
}}
'''
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text()
    a=source.index('static BOOL FlagReadInterface(void){');b=source.index('static id ObjectValue(',a)
    source=source[:a]+'static BOOL FlagReadInterface(void){return YES;}\n'+source[b:]
    source=source.replace('!FlagReadInterface() || !dlopen("/System/Library/PrivateFrameworks/ReminderKitInternal.framework/ReminderKitInternal",RTLD_LAZY|RTLD_LOCAL)','!FlagReadInterface()')
    for actual,fake in [('REMStore','FakeStore'),('REMObjectID','FakeID'),('REMRemindersListDataView_RecentlyDeletedInvocation','FakeInvocation')]:source=source.replace('NSClassFromString(@"'+actual+'")','NSClassFromString(@"'+fake+'")')
    with tempfile.TemporaryDirectory(prefix='recovery-view-contract-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(source+'\n'+FIXTURE)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        def call(s):return json.loads(subprocess.run([str(p/'contract'),s],capture_output=True,text=True,check=True,timeout=5).stdout)
        yes=call('present');assert yes['found_as_root'] is True and yes['data_mutated'] is False and yes['view']=='provider_recently_deleted' and len(yes['reported_child_ids'])==1 and yes['children_complete'] is False
        child=call('child');assert child['found_in_view'] is True and child['found_as_root'] is False and child['parent_id']==child['recovery_root_id'] and child['children_status']=='provider_reported' and child['reported_child_ids']==[]
        tree=call('tree_child');assert tree['data_mutated'] is False and tree['hierarchy_complete'] is False and tree['observation_consistency']=='single_provider_view' and len(tree['nodes'])==2 and tree['nodes'][1]['parent_id']==tree['root_id']
        roots=call('list_present');assert roots['items'][0]['title']=='Synthetic recovery title' and roots['done'] is True and roots['next_offset']==1 and roots['data_mutated'] is False
        page=call('list_out_of_range');assert page['items']==[] and page['done'] is True and page['next_offset']==5000
        assert call('list_missing_title')['error']=='private_metadata_unavailable' and call('list_foreign_reminder')['error']=='private_metadata_unavailable'
        missing=call('absent');assert missing['found_in_view'] is False and missing['recovery_root_id'] is None
        for scenario in ('foreign','duplicate_account','unsupported','error','duplicate','malformed','bad_children','cycle','too_many'):
            assert call(scenario)['error']=='private_metadata_unavailable',scenario
            assert call('tree_'+scenario)['error']=='private_metadata_unavailable',scenario
        assert call('tree_absent')['error']=='private_metadata_unavailable'
    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['fixed_scoped_provider_view','true_false_boolean','foreign_duplicate_denied_malformed_refused','no_mutation']}))
if __name__=='__main__':run()
