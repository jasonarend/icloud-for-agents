"""Shipped native pin writer with synthetic stores; no Apple data or OS consent."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_subtree_contract import fixture_source,shipped_source,ROOT
EXTRA=r'''
static BOOL extraPinEffect=NO,wrongPin=NO;
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*list=@"22222222-2222-4222-8222-222222222222";
 fixture=[FixtureReminder new];fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);fixture.list=[FixtureList new];fixture.list.remObjectID=Identity(@"REMCDList",list);fixture.list.account=fixture.account;fixture.list.storage=[FixtureListStorage new];fixture.list.storage.accountID=fixture.account.remObjectID;moveLists=@[fixture.list];moveReminders=@[];
 NSDictionary *identity=@{@"source_id":source,@"list_id":list,@"include_pinned":@YES};NSData *identityData=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];
 NSMutableDictionary *request=[@{@"source_id":source,@"list_id":list,@"pinned":@YES,@"expected_features":[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(identityData) options:0 error:NULL]} mutableCopy];__block int gates=0;
 NSDictionary *(^call)(BOOL)=^NSDictionary *(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersSetListPinned([NSJSONSerialization dataWithJSONObject:request options:0 error:NULL],^{gates++;return allow;}) options:0 error:NULL];};
 NSDictionary *closed=call(NO);NSCAssert([closed[@"category"] isEqual:@"native_dispatch_cancelled"] && gates==1 && flagSaves==0,@"closed pin gate never saves: %@",closed);
 NSDictionary *good=call(YES);NSCAssert([good[@"result"][@"pinned"] isEqual:@YES] && flagSaves==1 && gates==2,@"exact pin saved once: %@",good);int before=gates;
 extraPinEffect=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"unexpected staged field refused");extraPinEffect=NO;
 wrongPin=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"setter mismatch refused");wrongPin=NO;
 extraTracked=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"extra reminder refused");extraTracked=NO;
 wrongTracked=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"tracked wrapper mismatch refused");wrongTracked=NO;
 fixture.list.isPinned=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"stale pin refused");fixture.list.isPinned=NO;
 fixture.list.isShared=YES;request[@"expected_features"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(identityData) options:0 error:NULL];NSCAssert(call(YES)[@"error"] && gates==before,@"shared list refused even with matching before state");fixture.list.isShared=NO;request[@"expected_features"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(identityData) options:0 error:NULL];
 request[@"pinned"]=@1;NSCAssert(call(YES)[@"error"] && gates==before,@"numeric pin refused");request[@"pinned"]=@YES;
 saveFails=YES;NSCAssert([call(YES)[@"mutation_dispatched"] isEqual:@YES] && flagSaves==2,@"failed save retains uncertainty");saveFails=NO;
 fixture.list.isPinned=YES;request[@"pinned"]=@NO;request[@"expected_features"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(identityData) options:0 error:NULL];NSCAssert([call(YES)[@"result"][@"pinned"] isEqual:@NO] && flagSaves==3,@"exact unpin saves once");
}}
'''
def run():
    fixture=fixture_source().replace('@interface FixtureListChange:NSObject',EXTRA+'\n@interface FixtureListChange:NSObject\n@property BOOL isPinned;\n@property NSSet *changedKeys;')
    fixture=fixture.replace('@implementation FixtureListChange','@implementation FixtureListChange\n-(void)setIsPinned:(BOOL)value{_isPinned=wrongPin?!value:value;self.changedKeys=[NSSet setWithArray:extraPinEffect?@[@"pinnedDate",@"name"]:@[@"pinnedDate"]];}')
    fixture=fixture.replace('@interface FixtureSave:NSObject','@interface FixtureSave:NSObject\n@property FixtureListChange *pinChange;\n-(id)trackedListChangeItems;')
    fixture=fixture.replace('v.destination=list;return v;','v.destination=list;self.pinChange=v;return v;')
    fixture=fixture.replace('@implementation FixtureSave','@implementation FixtureSave\n-(id)trackedListChangeItems{FixtureTracked *v=[FixtureTracked new];v.valueWithoutPerformingCopy=wrongTracked?[NSObject new]:self.pinChange;return @{fixture.list.remObjectID:v};}')
    with tempfile.TemporaryDirectory(prefix='native-list-pin-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(shipped_source()+'\n'+fixture+'\n'+MAIN)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
    return {'status':'passed','apple_store_opened':False,'exact_pin_unpin_single_save':True,'closed_gate_stale_state_and_unreviewed_changes_refused':True,'failed_save_remains_uncertain':True}
if __name__=='__main__':print(json.dumps(run()))
