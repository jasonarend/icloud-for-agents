"""Execute fixed list-group staging with isolated in-memory native objects."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_groups_contract import groups_fixture
from native_reminder_subtree_contract import shipped_source,ROOT
EXTRA=r'''
static NSMutableDictionary *listChanges;
static FixtureAccountChange *activeAccount;
static BOOL badListKey=NO,badParent=NO,badOrder=NO;
@interface FixtureGroupContext:NSObject
@property FixtureGroupChange *group;
-(id)fetchCustomSmartListsWithError:(NSError**)error;
-(void)addListChangeItem:(FixtureGroupChange*)item;
@end
static void Place(FixtureGroupChange *item,FixtureGroupChange *anchor,BOOL after,FixtureGroupChange *group){
 item.storage.parentAccountID=fixture.account.remObjectID;item.storage.parentListID=badParent?fixture.account.remObjectID:group.storage.objectID;item.changedKeys=[NSSet setWithArray:badListKey?@[@"parentAccountID",@"parentListID",@"name"]:@[@"parentAccountID",@"parentListID"]];
 NSMutableArray *order=[activeAccount.storage.listIDsMergeableOrdering.orderedSet.array mutableCopy];[order removeObject:item.storage.objectID.uuid.UUIDString];NSUInteger at=anchor?[order indexOfObject:anchor.storage.objectID.uuid.UUIDString]+(after?1:0):0;[order insertObject:item.storage.objectID.uuid.UUIDString atIndex:at];if(badOrder)[order exchangeObjectAtIndex:0 withObjectAtIndex:order.count-1];activeAccount.storage.listIDsMergeableOrdering.orderedSet=[NSOrderedSet orderedSetWithArray:order];activeAccount.changedKeys=[NSSet setWithObject:@"listIDsMergeableOrdering"];
}
@implementation FixtureGroupContext
-(id)fetchCustomSmartListsWithError:(NSError**)error{return @[];}
-(void)addListChangeItem:(FixtureGroupChange*)item{Place(item,nil,NO,self.group);}
@end
'''
SAVE=r'''
@implementation FixtureSave
-(id)initWithStore:(id)store{self=[super init];listChanges=[NSMutableDictionary dictionary];activeAccount=nil;return self;}
-(id)updateAccount:(id)account{if(!activeAccount){activeAccount=[FixtureAccountChange new];activeAccount.storage=[FixtureAccountStorage new];activeAccount.storage.listIDsMergeableOrdering=[FixtureMergeOrder new];activeAccount.storage.listIDsMergeableOrdering.orderedSet=groupOrder;activeAccount.changedKeys=[NSSet set];}return activeAccount;}
-(id)updateList:(FixtureList*)list{[self updateAccount:list.account];NSString *identity=list.remObjectID.uuid.UUIDString;FixtureGroupChange *c=listChanges[identity];if(!c){c=[FixtureGroupChange new];c.storage=[FixtureListStorage new];c.storage.objectID=list.remObjectID;c.storage.accountID=list.storage.accountID;c.storage.parentAccountID=list.storage.parentAccountID;c.storage.parentListID=list.storage.parentListID;c.changedKeys=[NSSet set];listChanges[identity]=c;}return c;}
-(id)trackedAccountChangeItems{FixtureTracked *w=[FixtureTracked new];w.valueWithoutPerformingCopy=activeAccount;return @{fixture.account.remObjectID:w};}
-(id)trackedListChangeItems{NSMutableDictionary *v=[NSMutableDictionary dictionary];for(FixtureGroupChange *c in listChanges.allValues){FixtureTracked *w=[FixtureTracked new];w.valueWithoutPerformingCopy=wrongTracked?[NSObject new]:c;v[c.storage.objectID]=w;}return v;}
-(id)trackedReminderChangeItems{return extraGroupTask?@{@"unreviewed":@YES}:@{};}
-(id)trackedListSectionChangeItems{return @{};}
-(id)trackedSmartListChangeItems{return @{};}
-(id)trackedSmartListSectionChangeItems{return @{};}
-(id)trackedTemplateChangeItems{return @{};}
-(id)trackedTemplateSectionChangeItems{return @{};}
-(BOOL)saveSynchronouslyWithError:(NSError**)error{flagSaves++;if(saveFails){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:nil];return NO;}return YES;}
@end
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*listID=@"22222222-2222-4222-8222-222222222222",*groupID=@"33333333-3333-4333-8333-333333333333",*anchorID=@"44444444-4444-4444-8444-444444444444";
 fixture=[FixtureReminder new];fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);groupAccounts=@[fixture.account];NSMutableArray *values=[NSMutableArray array];
 for(NSString *identity in @[listID,groupID,anchorID]){FixtureList *list=[FixtureList new];list.account=fixture.account;list.remObjectID=Identity(@"REMCDList",identity);list.storage=[FixtureListStorage new];list.storage.objectID=list.remObjectID;list.storage.accountID=fixture.account.remObjectID;list.storage.parentAccountID=fixture.account.remObjectID;list.storage.isGroup=![identity isEqual:listID];list.storage.name=identity;[values addObject:list];}moveLists=values;groupValues=@[values[1],values[2]];FixtureList *list=values[0],*group=values[1];fixture.list=list;fixtureGroupChildren=@[];groupOrder=[NSOrderedSet orderedSetWithArray:@[groupID,listID,anchorID]];
 NSMutableDictionary *r=[@{@"operation":@"group_list",@"source_id":source,@"list_id":listID,@"fields":@{@"group_id":groupID}}mutableCopy];
 void(^bind)(void)=^{r[@"before_inventory"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupInventory([NSJSONSerialization dataWithJSONObject:@{@"source_id":source} options:0 error:NULL]) options:0 error:NULL];r[@"before_parent"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListParent([NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"list_id":listID} options:0 error:NULL]) options:0 error:NULL];r[@"before_members"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupLists([NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"group_id":groupID,@"limit":@100,@"offset":@0,@"include_smart_lists":@YES} options:0 error:NULL]) options:0 error:NULL];};bind();
 __block int gates=0;NSDictionary*(^call)(BOOL)=^NSDictionary*(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersAssignListGroup([NSJSONSerialization dataWithJSONObject:r options:0 error:NULL],^{gates++;return allow;}) options:0 error:NULL];};
 NSDictionary *closed=call(NO);NSCAssert([closed[@"category"]isEqual:@"native_dispatch_cancelled"] && flagSaves==0,@"closed staging never saves: %@",closed);NSCAssert(call(YES)[@"result"] && flagSaves==1,@"group existing list once");int prior=gates;
 badListKey=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"extra source fields refused");badListKey=NO;badParent=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"wrong parent refused");badParent=NO;badOrder=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"other list reordering refused");badOrder=NO;extraGroupTask=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"task changes refused");extraGroupTask=NO;wrongTracked=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"wrong wrapper refused");wrongTracked=NO;
 list.storage.parentListID=group.remObjectID;list.parentList=group;fixtureGroupChildren=@[list];groupOrder=[NSOrderedSet orderedSetWithArray:@[listID,groupID,anchorID]];r[@"operation"]=@"ungroup_list";r[@"fields"]=@{@"anchor_id":anchorID,@"placement":@"before"};bind();NSDictionary *ungroup=call(YES);NSCAssert(ungroup[@"result"] && flagSaves==2,@"ungroup only source relative to exact root anchor: %@ / %@",ungroup,r);
 saveFails=YES;NSCAssert([call(YES)[@"mutation_dispatched"]isEqual:@YES] && flagSaves==3,@"failed native save remains uncertain");saveFails=NO;prior=gates;group.isShared=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"shared group refused");
}}
'''
def list_groups_fixture():
    fixture=groups_fixture()
    fixture=fixture.replace('@interface FixtureAccountChange:NSObject','@class FixtureGroupChange;\n@interface FixtureAccountChange:NSObject\n-(void)insertListChangeItem:(id)item beforeListChangeItem:(id)anchor;\n-(void)insertListChangeItem:(id)item afterListChangeItem:(id)anchor;')
    fixture=fixture.replace('@interface FixtureGroupChange:NSObject','@interface FixtureGroupChange:NSObject\n-(id)sublistContext;')
    point=fixture.index('@implementation FixtureAccountChange');fixture=fixture[:point]+fixture[point:].replace('@implementation FixtureAccountChange @end','@implementation FixtureAccountChange\n-(void)insertListChangeItem:(id)item beforeListChangeItem:(id)anchor{Place(item,anchor,NO,nil);}\n-(void)insertListChangeItem:(id)item afterListChangeItem:(id)anchor{Place(item,anchor,YES,nil);}\n@end',1)
    point=fixture.index('@implementation FixtureAccountChange');fixture=fixture[:point]+'static void Place(FixtureGroupChange*,FixtureGroupChange*,BOOL,FixtureGroupChange*);\n'+fixture[point:]
    point=fixture.index('@implementation FixtureGroupChange');fixture=fixture[:point]+EXTRA[:EXTRA.index('static void Place(')]+'\n'+fixture[point:]
    fixture=fixture.replace('@implementation FixtureGroupChange\n','@implementation FixtureGroupChange\n-(id)sublistContext{FixtureGroupContext *c=[FixtureGroupContext new];c.group=self;return c;}\n')
    fixture=fixture.replace('@interface FixtureList:NSObject','@interface FixtureList:NSObject\n-(id)sublistContext;').replace('@implementation FixtureList\n','@implementation FixtureList\n-(id)sublistContext{return [FixtureGroupContext new];}\n')
    a=fixture.index('@implementation FixtureSave');b=fixture.index('\n@end',a)+len('\n@end');fixture=fixture[:a]+EXTRA[EXTRA.index('static void Place('):]+SAVE+fixture[b:]
    return fixture

def run():
    fixture=list_groups_fixture()
    with tempfile.TemporaryDirectory(prefix='native-list-groups-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(shipped_source()+'\n'+fixture+'\n'+MAIN)
        r=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr
        r=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert r.returncode==0,r.stderr
    return {'status':'passed','apple_store_opened':False,'group_ungroup_single_gated_save':True,'unreviewed_parent_order_content_and_wrappers_refused':True,'failed_save_retains_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
