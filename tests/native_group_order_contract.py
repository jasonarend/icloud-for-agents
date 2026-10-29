"""Actual root-group ordering primitive with synthetic native stores only."""
import json,subprocess,tempfile
from pathlib import Path
from native_list_groups_contract import list_groups_fixture
from native_reminder_subtree_contract import shipped_source,ROOT
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*groupID=@"33333333-3333-4333-8333-333333333333",*anchorID=@"44444444-4444-4444-8444-444444444444",*childID=@"55555555-5555-4555-8555-555555555555";
 fixture=[FixtureReminder new];fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);groupAccounts=@[fixture.account];NSMutableArray *values=[NSMutableArray array];for(NSString *identity in @[groupID,anchorID,childID]){FixtureList *v=[FixtureList new];v.account=fixture.account;v.remObjectID=Identity(@"REMCDList",identity);v.storage=[FixtureListStorage new];v.storage.objectID=v.remObjectID;v.storage.accountID=fixture.account.remObjectID;v.storage.parentAccountID=fixture.account.remObjectID;v.storage.isGroup=![identity isEqual:childID];v.storage.name=identity;[values addObject:v];}FixtureList *group=values[0],*child=values[2];child.storage.parentListID=group.remObjectID;child.parentList=group;moveLists=values;groupValues=@[values[0],values[1]];fixtureGroupChildren=@[child];groupOrder=[NSOrderedSet orderedSetWithArray:@[groupID,childID,anchorID]];fixture.list=group;
 NSDictionary *inventory=[NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupInventory([NSJSONSerialization dataWithJSONObject:@{@"source_id":source} options:0 error:NULL]) options:0 error:NULL],*members=[NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupLists([NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"group_id":groupID,@"limit":@100,@"offset":@0,@"include_smart_lists":@YES} options:0 error:NULL]) options:0 error:NULL];NSMutableDictionary *r=[@{@"source_id":source,@"group_id":groupID,@"anchor_id":anchorID,@"placement":@"after",@"before_inventory":inventory,@"before_members":members}mutableCopy];__block int gates=0;
 NSDictionary*(^call)(BOOL)=^NSDictionary*(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReorderRootGroup([NSJSONSerialization dataWithJSONObject:r options:0 error:NULL],^{gates++;return allow;}) options:0 error:NULL];};
 NSDictionary *closed=call(NO);NSCAssert([closed[@"category"]isEqual:@"native_dispatch_cancelled"] && gates==1 && flagSaves==0,@"closed root-group order never saves: %@",closed);NSDictionary *good=call(YES);NSCAssert(good[@"result"] && gates==2 && flagSaves==1,@"one group-order gated save: %@",good);int prior=gates;
 badListKey=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"unreviewed group field refused");badListKey=NO;badParent=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"changed parent refused");badParent=NO;badOrder=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"other order changes refused");badOrder=NO;extraGroupTask=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"task staging refused");extraGroupTask=NO;wrongTracked=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"wrong tracked wrapper refused");wrongTracked=NO;
 saveFails=YES;NSCAssert([call(YES)[@"mutation_dispatched"]isEqual:@YES] && flagSaves==2,@"failed group-order save remains uncertain");saveFails=NO;group.isShared=YES;NSCAssert(call(YES)[@"error"] && gates==prior+1,@"shared group refused");group.isShared=NO;r[@"placement"]=@"before";r[@"anchor_id"]=childID;NSCAssert(call(YES)[@"error"],@"nested physical anchor refused");
}}
'''
def run():
 fixture=list_groups_fixture().replace('c.storage.objectID=list.remObjectID;', 'c.storage.isGroup=list.storage.isGroup;c.storage.objectID=list.remObjectID;')
 old='item.changedKeys=[NSSet setWithArray:badListKey?@[@"parentAccountID",@"parentListID",@"name"]:@[@"parentAccountID",@"parentListID"]];'
 new='item.changedKeys=[NSSet setWithArray:badListKey?@[@"parentAccountID",@"name"]:@[@"parentAccountID"]];';assert old in fixture;fixture=fixture.replace(old,new)
 with tempfile.TemporaryDirectory(prefix='native-group-order-') as directory:
  p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(shipped_source()+'\n'+fixture+'\n'+MAIN)
  r=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr
  r=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert r.returncode==0,r.stderr
 return {'status':'passed','apple_store_opened':False,'populated_group_order_single_gated_save':True,'unexpected_parent_fields_tasks_order_and_wrappers_refused':True,'failed_save_retains_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
