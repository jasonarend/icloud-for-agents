"""Guard native root-group inventory and creation using in-memory framework objects."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_subtree_contract import fixture_source,shipped_source,ROOT
EXTRA=r'''
static NSArray *groupAccounts,*groupValues;
static NSOrderedSet *groupOrder;
static BOOL extraGroupKey=NO,wrongGroupDefaults=NO,wrongGroupOrder=NO,extraGroupTask=NO;
@interface FixtureGroups:NSObject
-(id)fetchGroupsWithError:(NSError**)error;
@end
@implementation FixtureGroups
-(id)fetchGroupsWithError:(NSError**)error{return groupValues;}
@end
@interface FixtureMergeOrder:NSObject
@property NSOrderedSet *orderedSet;
@end
@implementation FixtureMergeOrder @end
@interface FixtureAccountStorage:NSObject
@property FixtureMergeOrder *listIDsMergeableOrdering;
@end
@implementation FixtureAccountStorage @end
@interface FixtureAccountChange:NSObject
@property FixtureAccountStorage *storage;
@property FixtureGroups *groupContext;
@property NSSet *changedKeys;
@end
@implementation FixtureAccountChange @end
@interface FixtureGroupChange:NSObject
@property FixtureListStorage *storage;
@property NSSet *changedKeys;
@end
@implementation FixtureGroupChange
-(void)setValue:(id)value forUndefinedKey:(NSString*)key{NSCAssert([key isEqual:@"name"],@"only exact name field");[(id)self.storage setValue:value forKey:@"name"];self.changedKeys=[NSSet setWithArray:extraGroupKey?@[@"name",@"sortingStyle"]:@[@"name"]];}
@end
static NSSet *InitialGroupKeys(void){return [NSSet setWithArray:@[@"reminderIDsMergeableOrdering",@"showingLargeAttachments",@"reminderIDsToUndelete",@"objectID",@"isGroup",@"reminderIDsMergeableOrderingData",@"shouldUpdateSectionsOrdering",@"minimumSupportedVersion",@"shouldAutoCategorizeItems",@"shouldSupportGroceryShoppingSession",@"childSmartListIDsToUndelete",@"daIsImmutable",@"parentAccountID",@"remindersICSDisplayOrderChanged",@"name",@"shouldCategorizeGroceryItems",@"childListIDsToUndelete",@"reminderIDsOrderingHints",@"sharingStatus",@"accountID",@"daDisplayOrder",@"shouldSuggestConversionToGroceryList",@"sectionIDsToUndelete",@"daIsNotificationsCollection",@"daIsReadOnly",@"sortingStyle",@"daIsEventOnlyContainer",@"isPlaceholder"]];}
'''
STORAGE=r'''
@property FixtureID *objectID;
@property FixtureID *parentAccountID;
@property NSString *sortingStyle;
@property NSData *reminderIDsMergeableOrderingData;
@property NSOrderedSet *reminderIDsMergeableOrdering;
@property NSDictionary *reminderIDsOrderingHints;
@property NSSet *reminderIDsToUndelete;
@property NSSet *childListIDsToUndelete;
@property NSSet *childSmartListIDsToUndelete;
@property NSSet *sectionIDsToUndelete;
'''
SAVE=r'''
@implementation FixtureSave
-(id)initWithStore:(id)store{return [super init];}
-(id)updateAccount:(id)account{FixtureAccountChange *c=[FixtureAccountChange new];c.storage=[FixtureAccountStorage new];c.storage.listIDsMergeableOrdering=[FixtureMergeOrder new];c.storage.listIDsMergeableOrdering.orderedSet=groupOrder;c.groupContext=[FixtureGroups new];c.changedKeys=[NSSet set];self.groupAccount=c;return c;}
-(id)updateList:(FixtureList*)list{
 [self updateAccount:list.account];FixtureGroupChange *g=[FixtureGroupChange new];g.storage=[FixtureListStorage new];g.storage.name=list.storage.name;g.storage.isGroup=list.storage.isGroup;g.storage.objectID=list.remObjectID;g.storage.accountID=list.storage.accountID;g.storage.parentAccountID=list.storage.parentAccountID;g.storage.parentListID=list.storage.parentListID;g.changedKeys=[NSSet set];self.createdGroup=g;return g;
}
-(id)addGroupWithName:(NSString*)name toAccountGroupContextChangeItem:(id)context groupObjectID:(FixtureID*)identity{
 FixtureGroupChange *g=[FixtureGroupChange new];g.storage=[FixtureListStorage new];g.changedKeys=InitialGroupKeys();if(extraGroupKey){NSMutableSet *keys=[g.changedKeys mutableCopy];[keys addObject:@"notes"];g.changedKeys=keys;}
 g.storage.name=name;g.storage.isGroup=YES;g.storage.objectID=identity;g.storage.accountID=fixture.account.remObjectID;g.storage.parentAccountID=fixture.account.remObjectID;g.storage.sortingStyle=@"manual";g.storage.reminderIDsMergeableOrdering=[NSOrderedSet orderedSet];g.storage.reminderIDsOrderingHints=@{};g.storage.reminderIDsToUndelete=[NSSet set];g.storage.childListIDsToUndelete=[NSSet set];g.storage.childSmartListIDsToUndelete=[NSSet set];g.storage.sectionIDsToUndelete=[NSSet set];
 NSMutableArray *order=[groupOrder.array mutableCopy];[order addObject:identity.uuid.UUIDString];if(wrongGroupOrder)[order exchangeObjectAtIndex:0 withObjectAtIndex:1];self.groupAccount.storage.listIDsMergeableOrdering.orderedSet=[NSOrderedSet orderedSetWithArray:order];self.groupAccount.changedKeys=[NSSet setWithObject:@"listIDsMergeableOrdering"];self.createdGroup=g;return g;
}
-(id)trackedAccountChangeItems{FixtureTracked *w=[FixtureTracked new];w.valueWithoutPerformingCopy=wrongTracked?[NSObject new]:self.groupAccount;return @{fixture.account.remObjectID:w};}
-(id)trackedListChangeItems{FixtureTracked *w=[FixtureTracked new];w.valueWithoutPerformingCopy=self.createdGroup;return @{self.createdGroup.storage.objectID:w};}
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
 NSString *source=@"11111111-1111-4111-8111-111111111111",*list=@"22222222-2222-4222-8222-222222222222",*groupID=@"33333333-3333-4333-8333-333333333333";
 fixture=[FixtureReminder new];fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);groupAccounts=@[fixture.account];groupValues=@[];groupOrder=[NSOrderedSet orderedSetWithObject:list];
 NSData *scope=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source} options:0 error:NULL];NSDictionary *(^read)(void)=^NSDictionary*{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupInventory(scope) options:0 error:NULL];};
 NSDictionary *empty=read();NSCAssert([empty[@"groups"]isEqual:@[]] && [empty[@"account_order"]count]==1,@"selected empty inventory: %@",empty);
 NSMutableDictionary *request=[@{@"source_id":source,@"group_id":groupID,@"title":@"Synthetic native group",@"before":empty}mutableCopy];__block int gates=0;
 NSDictionary *(^call)(BOOL)=^NSDictionary*(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersCreateRootGroup([NSJSONSerialization dataWithJSONObject:request options:0 error:NULL],^{gates++;return allow;}) options:0 error:NULL];};
 NSDictionary *closed=call(NO);NSCAssert([closed[@"category"]isEqual:@"native_dispatch_cancelled"] && gates==1 && flagSaves==0,@"closed group gate never saves: %@",closed);
 NSDictionary *good=call(YES);NSCAssert([good[@"result"][@"id"]isEqual:groupID] && gates==2 && flagSaves==1,@"exact empty group saved once: %@",good);int before=gates;
 extraGroupKey=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"extra new-group field refused");extraGroupKey=NO;
 wrongGroupDefaults=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"nondefault side effect refused");wrongGroupDefaults=NO;
 wrongGroupOrder=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"reordered existing lists refused");wrongGroupOrder=NO;
 extraGroupTask=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"unreviewed native task refused");extraGroupTask=NO;
 wrongTracked=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"wrong account wrapper refused");wrongTracked=NO;
 groupOrder=[NSOrderedSet orderedSet];NSCAssert(call(YES)[@"error"] && gates==before,@"account inventory drift refused");groupOrder=[NSOrderedSet orderedSetWithObject:list];
 saveFails=YES;NSCAssert([call(YES)[@"mutation_dispatched"]isEqual:@YES] && flagSaves==2,@"failed save remains uncertain");saveFails=NO;
 FixtureList *group=[FixtureList new];group.remObjectID=Identity(@"REMCDList",groupID);group.account=fixture.account;group.storage=[FixtureListStorage new];group.storage.isGroup=YES;group.storage.accountID=fixture.account.remObjectID;group.storage.parentAccountID=fixture.account.remObjectID;group.storage.name=@"Synthetic existing group";groupValues=@[group];groupOrder=[NSOrderedSet orderedSetWithArray:@[list,groupID]];
 NSCAssert([read()[@"groups"]count]==1,@"populated selected root inventory");group.storage.parentListID=Identity(@"REMCDList",list);NSCAssert(read()[@"error"],@"nested unsupported group refused");group.storage.parentListID=nil;
 NSDictionary *(^rename)(BOOL)=^NSDictionary*(BOOL allow){NSDictionary *r=@{@"source_id":source,@"group_id":groupID,@"title":@"Synthetic renamed group",@"before":read()};return [NSJSONSerialization JSONObjectWithData:ICloudRemindersRenameRootGroup([NSJSONSerialization dataWithJSONObject:r options:0 error:NULL],^{gates++;return allow;}) options:0 error:NULL];};moveLists=@[group];int saves=flagSaves;
 NSCAssert([rename(NO)[@"category"]isEqual:@"native_dispatch_cancelled"] && flagSaves==saves,@"rename closed gate never saves");NSCAssert(rename(YES)[@"result"] && flagSaves==saves+1,@"exact group name saved once");before=gates;
 extraGroupKey=YES;NSCAssert(rename(YES)[@"error"] && gates==before,@"unreviewed rename field refused");extraGroupKey=NO;
 extraGroupTask=YES;NSCAssert(rename(YES)[@"error"] && gates==before,@"rename cannot mutate reminders");extraGroupTask=NO;
 saveFails=YES;NSCAssert([rename(YES)[@"mutation_dispatched"]isEqual:@YES],@"rename failed save is uncertain");saveFails=NO;
 group.isShared=YES;NSCAssert(read()[@"error"],@"shared group refused");group.isShared=NO;group.storage.accountID=Identity(@"REMCDAccount",list);NSCAssert(read()[@"error"],@"foreign storage refused");
}}
'''
def groups_fixture():
    fixture=fixture_source().replace('@interface FixtureAccount:NSObject','@interface FixtureAccount:NSObject\n-(id)groupContext;\n-(id)listIDsOrdering;')
    fixture=fixture.replace('@property NSString *name;','@property NSString *name;\n'+STORAGE)
    fixture=fixture.replace('@implementation FixtureListStorage','@implementation FixtureListStorage\n-(id)valueForUndefinedKey:(NSString*)key{return (wrongGroupDefaults && [key isEqual:@"sharingStatus"])?@1:@0;}')
    # Definitions precede uses; Objective-C classes only need forward declarations for properties.
    fixture=fixture.replace('@interface FixtureAccount:NSObject','@class FixtureListStorage,FixtureAccountChange,FixtureGroupChange;\nstatic NSArray *groupAccounts,*groupValues;static NSOrderedSet *groupOrder;static BOOL extraGroupKey=NO,wrongGroupDefaults=NO,wrongGroupOrder=NO,extraGroupTask=NO;\n@interface FixtureAccount:NSObject',1)
    extra=EXTRA[EXTRA.index('@interface FixtureGroups'):]
    spot=fixture.index('@implementation FixtureAccount');fixture=fixture[:spot]+extra+'\n'+fixture[spot:]
    fixture=fixture.replace('@implementation FixtureAccount\n','@implementation FixtureAccount\n-(id)groupContext{return [FixtureGroups new];}\n-(id)listIDsOrdering{return groupOrder;}')
    fixture=fixture.replace('@interface FixtureStore:NSObject','@interface FixtureStore:NSObject\n-(id)fetchAccountsWithError:(NSError**)error;').replace('@implementation FixtureStore','@implementation FixtureStore\n-(id)fetchAccountsWithError:(NSError**)error{return groupAccounts;}')
    fixture=fixture.replace('@interface FixtureSave:NSObject','@interface FixtureSave:NSObject\n@property FixtureAccountChange *groupAccount;\n@property FixtureGroupChange *createdGroup;\n-(id)updateAccount:(id)account;\n-(id)addGroupWithName:(id)name toAccountGroupContextChangeItem:(id)context groupObjectID:(id)identity;\n-(id)trackedAccountChangeItems;\n-(id)trackedListChangeItems;\n-(id)trackedListSectionChangeItems;\n-(id)trackedSmartListChangeItems;\n-(id)trackedSmartListSectionChangeItems;\n-(id)trackedTemplateChangeItems;\n-(id)trackedTemplateSectionChangeItems;')
    a=fixture.index('@implementation FixtureSave');b=fixture.index('\n@end',a)+len('\n@end');fixture=fixture[:a]+SAVE+fixture[b:]
    return fixture

def run():
    fixture=groups_fixture()
    with tempfile.TemporaryDirectory(prefix='native-reminder-groups-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(shipped_source()+'\n'+fixture+'\n'+MAIN)
        r=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr
        r=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert r.returncode==0,r.stderr
    return {'status':'passed','apple_store_opened':False,'exact_empty_group_single_save':True,'closed_gate_and_unreviewed_side_effects_refused':True,'failed_save_preserves_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
