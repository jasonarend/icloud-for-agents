"""Execute fixed native lifecycle staging against synthetic stores only."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_subtree_contract import fixture_source,shipped_source,ROOT
EXTRA=r'''
@class FixtureSave;
static FixtureSave *lifecycleSave;
static BOOL lifecycleDeleted=NO,extraLifecycleEffect=NO,wrongPending=NO;
static BOOL familyLifecycle=NO;
static NSString *familyRoot,*familyChild;
static void StageLeafRemoval(id value);
'''
METHODS=r'''
-(id)trackedListChangeItems{FixtureTracked *value=[FixtureTracked new];value.valueWithoutPerformingCopy=wrongTracked?[NSObject new]:self.lifecycleList;return self.lifecycleList?@{self.lifecycleList.destination.remObjectID:value}:@{};}
'''
FUNCTIONS=r'''
static void StageLeafRemoval(FixtureReminderChange *value){value.storage.listID=nil;value.storage.parentReminderID=nil;value.changedKeys=[NSSet setWithArray:extraLifecycleEffect?@[@"listID",@"titleDocumentData"]:@[@"listID"]];FixtureListChange *list=[lifecycleSave updateList:fixture.list];list.changedKeys=[NSSet setWithObject:@"reminderIDsMergeableOrdering"];}
NSData *ICloudRemindersRecoveryLookup(NSData *data){NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];BOOL child=familyLifecycle && [identity[@"id"] isEqual:familyChild];return EncodeFlag(@{@"protocol_version":@1,@"source_id":identity[@"source_id"],@"id":identity[@"id"],@"found_in_view":@(lifecycleDeleted),@"found_as_root":@(lifecycleDeleted&&!child),@"recovery_root_id":lifecycleDeleted?(familyLifecycle?familyRoot:identity[@"id"]):(id)[NSNull null],@"parent_id":child?(id)familyRoot:NSNull.null});}
NSData *ICloudRemindersRecoveryTree(NSData *data){NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];NSMutableArray *nodes=[NSMutableArray arrayWithObject:@{@"id":identity[@"id"],@"parent_id":NSNull.null,@"title":@"Synthetic leaf"}];if(familyLifecycle)[nodes addObject:@{@"id":familyChild,@"parent_id":familyRoot,@"title":@"Synthetic child"}];return EncodeFlag(@{@"protocol_version":@1,@"source_id":identity[@"source_id"],@"requested_id":identity[@"id"],@"root_id":identity[@"id"],@"nodes":nodes});}
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*listID=@"22222222-2222-4222-8222-222222222222",*task=@"33333333-3333-4333-8333-333333333333";
 fixture=[FixtureReminder new];fixture.remObjectID=Identity(@"REMCDReminder",task);fixture.titleAsString=@"Synthetic leaf";fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);FixtureList *list=[FixtureList new];list.remObjectID=Identity(@"REMCDList",listID);list.account=fixture.account;list.storage=[FixtureListStorage new];list.storage.accountID=fixture.account.remObjectID;fixture.list=list;list.graphReminders=@[fixture];moveLists=@[list];moveReminders=@[fixture];
 NSDictionary *node=@{@"id":task,@"parent_id":[NSNull null]},*identity=@{@"id":task,@"source_id":source,@"list_id":listID};NSMutableDictionary *remove=[identity mutableCopy];remove[@"source_nodes"]=@[node];__block int gates=0;
 NSDictionary *(^callRemove)(BOOL)=^NSDictionary *(BOOL open){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersRemoveRootLeaf([NSJSONSerialization dataWithJSONObject:remove options:0 error:NULL],^{gates++;return open;}) options:0 error:NULL];};
 NSDictionary *closed=callRemove(NO);NSCAssert([closed[@"category"] isEqual:@"native_dispatch_cancelled"] && gates==1 && flagSaves==0,@"removal stages without saving behind a closed gate: %@",closed);
 NSDictionary *good=callRemove(YES);NSCAssert([good[@"result"][@"id"] isEqual:task] && gates==2 && flagSaves==1,@"one exact native leaf removed in one save: %@",good);int before=gates;
 extraLifecycleEffect=YES;NSCAssert(callRemove(YES)[@"error"] && gates==before,@"unexpected reminder content change refused");extraLifecycleEffect=NO;
 extraTracked=YES;NSCAssert(callRemove(YES)[@"error"] && gates==before,@"additional reminder tracked refused");extraTracked=NO;
 wrongTracked=YES;NSCAssert(callRemove(YES)[@"error"] && gates==before,@"incorrect tracked wrapper refused");wrongTracked=NO;
 list.isShared=YES;NSCAssert(callRemove(YES)[@"error"] && gates==before,@"shared native list refused");list.isShared=NO;
 remove[@"source_nodes"]=@[];NSCAssert(callRemove(YES)[@"error"] && gates==before,@"changed graph refused");remove[@"source_nodes"]=@[node];
 saveFails=YES;NSCAssert([callRemove(YES)[@"mutation_dispatched"] isEqual:@YES] && flagSaves==2,@"failed removal save remains uncertain");saveFails=NO;
 lifecycleDeleted=YES;fixture.list=nil;list.graphReminders=@[];moveReminders=@[];NSDictionary *lookup=[NSJSONSerialization JSONObjectWithData:ICloudRemindersRecoveryLookup([NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL]) options:0 error:NULL],*tree=[NSJSONSerialization JSONObjectWithData:ICloudRemindersRecoveryTree([NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL]) options:0 error:NULL];
 NSMutableDictionary *restore=[identity mutableCopy];restore[@"title"]=@"Synthetic leaf";restore[@"destination_nodes"]=@[];restore[@"expected_recovery"]=@{@"lookup":lookup,@"tree":tree};
 NSDictionary *(^callRestore)(BOOL)=^NSDictionary *(BOOL open){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersRestoreRootLeaf([NSJSONSerialization dataWithJSONObject:restore options:0 error:NULL],^{gates++;return open;}) options:0 error:NULL];};
 closed=callRestore(NO);NSCAssert([closed[@"category"] isEqual:@"native_dispatch_cancelled"] && flagSaves==2,@"closed restore gate never saves: %@",closed);
 good=callRestore(YES);NSCAssert([good[@"result"][@"id"] isEqual:task] && flagSaves==3,@"exact preexisting ID restored in one gated save: %@",good);before=gates;
 wrongPending=YES;NSCAssert(callRestore(YES)[@"error"] && gates==before,@"foreign pending restore ID refused");wrongPending=NO;
 extraLifecycleEffect=YES;NSCAssert(callRestore(YES)[@"error"] && gates==before,@"unreviewed list effect refused");extraLifecycleEffect=NO;
 wrongTracked=YES;NSCAssert(callRestore(YES)[@"error"] && gates==before,@"restore tracked wrapper must bind exact list");wrongTracked=NO;
 restore[@"title"]=@"Changed";NSCAssert(callRestore(YES)[@"error"] && gates==before,@"changed recovery title refused");restore[@"title"]=@"Synthetic leaf";
 list.isShared=YES;NSCAssert(callRestore(YES)[@"error"] && gates==before,@"shared restore list refused");list.isShared=NO;
 saveFails=YES;NSCAssert([callRestore(YES)[@"mutation_dispatched"] isEqual:@YES] && flagSaves==4,@"failed restore save remains uncertain");
 saveFails=NO;familyLifecycle=YES;familyRoot=task;familyChild=@"55555555-5555-4555-8555-555555555555";lifecycleDeleted=NO;fixture.list=list;
 FixtureReminder *child=[FixtureReminder new];child.remObjectID=Identity(@"REMCDReminder",familyChild);child.account=fixture.account;child.list=list;child.parentReminder=fixture;child.isSubtask=YES;child.titleAsString=@"Synthetic child";
 list.graphReminders=@[fixture,child];moveReminders=@[fixture,child];remove[@"source_nodes"]=@[node,@{@"id":familyChild,@"parent_id":task}];before=gates;
 NSCAssert(callRemove(YES)[@"error"] && gates==before,@"ordinary leaf authority does not delete a family");
 remove[@"family_ids"]=@[task,familyChild];closed=callRemove(NO);NSCAssert([closed[@"category"] isEqual:@"native_dispatch_cancelled"] && flagSaves==4,@"family staging remains behind the gate: %@",closed);
 good=callRemove(YES);NSCAssert([good[@"result"][@"id"] isEqual:task] && flagSaves==5,@"explicit complete family can stage one root removal: %@",good);before=gates;
 remove[@"family_ids"]=@[task,source];NSCAssert(callRemove(YES)[@"error"] && gates==before,@"foreign family member refused");remove[@"family_ids"]=@[task,familyChild];
 FixtureReminder *grandchild=[FixtureReminder new];grandchild.remObjectID=Identity(@"REMCDReminder",@"66666666-6666-4666-8666-666666666666");grandchild.account=fixture.account;grandchild.list=list;grandchild.parentReminder=child;grandchild.isSubtask=YES;
 list.graphReminders=@[fixture,child,grandchild];moveReminders=@[fixture,child,grandchild];remove[@"source_nodes"]=@[node,@{@"id":familyChild,@"parent_id":task},@{@"id":@"66666666-6666-4666-8666-666666666666",@"parent_id":familyChild}];NSCAssert(callRemove(YES)[@"error"] && gates==before,@"nested family refuses before mutation");
 lifecycleDeleted=YES;fixture.list=nil;list.graphReminders=@[];moveReminders=@[];lookup=[NSJSONSerialization JSONObjectWithData:ICloudRemindersRecoveryLookup(EncodeFlag(identity)) options:0 error:NULL];tree=[NSJSONSerialization JSONObjectWithData:ICloudRemindersRecoveryTree(EncodeFlag(identity)) options:0 error:NULL];restore[@"expected_recovery"]=@{@"lookup":lookup,@"tree":tree};
 NSCAssert(callRestore(YES)[@"error"] && gates==before,@"ordinary leaf restore cannot admit extra family nodes");
 restore[@"family_ids"]=@[task,familyChild];closed=callRestore(NO);NSCAssert([closed[@"category"] isEqual:@"native_dispatch_cancelled"] && flagSaves==5,@"closed family restore gate");
 good=callRestore(YES);NSCAssert([good[@"result"][@"id"] isEqual:task] && flagSaves==6,@"exact native recovery family accepted: %@",good);before=gates;
 restore[@"family_ids"]=@[task,source];NSCAssert(callRestore(YES)[@"error"] && gates==before,@"unreviewed recovery identity refused");
}}
'''

def run():
    fixture=EXTRA+'\n'+fixture_source().replace('@interface FixtureMoveStorage:NSObject','@interface FixtureMoveStorage:NSObject\n@property FixtureID *accountID;')
    fixture=fixture.replace('@interface FixtureListStorage:NSObject','@interface FixtureListStorage:NSObject\n@property NSSet *reminderIDsToUndelete;')
    fixture=fixture.replace('@interface FixtureReminder:NSObject','@interface FixtureReminder:NSObject\n@property NSString *titleAsString;')
    fixture=fixture.replace('@interface FixtureReminderChange:NSObject','@interface FixtureReminderChange:NSObject\n-(id)removeFromListAllowingUndo;')
    fixture=fixture.replace('@implementation FixtureReminderChange','@implementation FixtureReminderChange\n-(id)removeFromListAllowingUndo{StageLeafRemoval(self);return [NSObject new];}')
    fixture=fixture.replace('@interface FixtureListChange:NSObject','@interface FixtureListChange:NSObject\n@property NSSet *changedKeys;\n@property FixtureListStorage *storage;\n-(void)undeleteRemindersWithoutUndoWithIDs:(id)identities;')
    fixture=fixture.replace('@implementation FixtureListChange','@implementation FixtureListChange\n-(void)undeleteRemindersWithoutUndoWithIDs:(NSArray *)identities{self.storage.reminderIDsToUndelete=[NSSet setWithArray:wrongPending?@[Identity(@"REMCDReminder",@"AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA")]:identities];self.changedKeys=[NSSet setWithArray:extraLifecycleEffect?@[@"reminderIDsToUndelete",@"reminderIDsMergeableOrdering",@"name"]:@[@"reminderIDsToUndelete",@"reminderIDsMergeableOrdering"]];}')
    fixture=fixture.replace('@interface FixtureSave:NSObject','@interface FixtureSave:NSObject\n@property FixtureListChange *lifecycleList;\n-(id)trackedListChangeItems;')
    fixture=fixture.replace('@implementation FixtureSave','@implementation FixtureSave\n'+METHODS)
    fixture=fixture.replace('self.moveChanges=[NSMutableDictionary dictionary];','self.moveChanges=[NSMutableDictionary dictionary];lifecycleSave=self;')
    fixture=fixture.replace('v.destination=list;return v;','v.destination=list;v.storage=[FixtureListStorage new];v.storage.accountID=[list account].remObjectID;self.lifecycleList=v;return v;')
    fixture=fixture.replace('v.storage.listID=[reminder list].remObjectID;','v.storage.listID=[reminder list].remObjectID;v.storage.accountID=[reminder account].remObjectID;')
    fixture=fixture.replace('return childCollision?fixture:nil;','return lifecycleDeleted?fixture:nil;')
    source=shipped_source().replace('NSData *ICloudRemindersRecoveryLookup(NSData *data){','NSData *UnusedLifecycleRecoveryLookup(NSData *data){').replace('NSData *ICloudRemindersRecoveryTree(NSData *data){','NSData *UnusedLifecycleRecoveryTree(NSData *data){')
    with tempfile.TemporaryDirectory(prefix='native-reminder-lifecycle-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(source+'\n'+fixture+'\n'+FUNCTIONS+'\n'+MAIN)
        value=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert value.returncode==0,value.stderr
        value=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert value.returncode==0,value.stderr
    return {'status':'passed','apple_store_opened':False,'root_leaf_delete_restore_exact_native_identity':True,'explicit_direct_family_scope_and_closed_gates':True,'closed_gates_and_unreviewed_effects_refused':True,'failed_saves_preserve_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
