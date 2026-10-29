"""Fixed native existing-leaf parent changes with in-memory stores and save gates."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_order_contract import order_fixture
from native_reminder_subtree_contract import shipped_source,ROOT
METHOD=r'''
-(void)insertReminderChangeItem:(FixtureReminderChange*)item adjacentToReminderChangeItem:(FixtureReminderChange*)adjacent isAfter:(BOOL)after withParentReminderChangeItem:(FixtureReminderChange*)parent{
 NSString *sourceID=nil,*anchorID=nil,*parentID=nil;for(NSString *identity in activeChanges){if(activeChanges[identity]==item)sourceID=identity;if(activeChanges[identity]==adjacent)anchorID=identity;if(activeChanges[identity]==parent)parentID=identity;}
 item.storage.listID=self.destination.remObjectID;item.storage.parentReminderID=parentID?Identity(@"REMCDReminder",wrongParent?sourceID:parentID):nil;item.changedKeys=[NSSet setWithArray:extraEffect?@[@"listID",@"parentReminderID",@"notes"]:@[@"listID",@"parentReminderID"]];NSMutableArray *values=[fixtureOrder.array mutableCopy];FixtureID *sourceObject=nil,*anchorObject=nil;for(FixtureID *v in values){if([v.uuid.UUIDString isEqual:sourceID])sourceObject=v;if([v.uuid.UUIDString isEqual:anchorID])anchorObject=v;}[values removeObject:sourceObject];NSUInteger at=anchorObject?[values indexOfObject:anchorObject]+(after?1:0):values.count;[values insertObject:sourceObject atIndex:at];if(wrongOrder)[values exchangeObjectAtIndex:0 withObjectAtIndex:1];self.storage.reminderIDsMergeableOrdering=[NSOrderedSet orderedSetWithArray:values];self.changedKeys=[NSSet setWithArray:@[@"reminderIDsMergeableOrdering",@"reminderIDsOrderingHints"]];
}
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*listID=@"22222222-2222-4222-8222-222222222222",*oldID=@"44444444-4444-4444-8444-444444444444",*leafID=@"55555555-5555-4555-8555-555555555555",*parentID=@"66666666-6666-4666-8666-666666666666",*siblingID=@"77777777-7777-4777-8777-777777777777";
 FixtureAccount *account=[FixtureAccount new];account.remObjectID=Identity(@"REMCDAccount",source);FixtureList *list=[FixtureList new];list.remObjectID=Identity(@"REMCDList",listID);list.account=account;list.storage=[FixtureListStorage new];list.storage.accountID=account.remObjectID;NSMutableArray *items=[NSMutableArray array];for(NSString *identity in @[oldID,leafID,siblingID,parentID]){FixtureReminder *v=[FixtureReminder new];v.account=account;v.list=list;v.remObjectID=Identity(@"REMCDReminder",identity);v.storage=[FixtureStorage new];v.storage.accountID=account.remObjectID;v.isSubtask=[@[leafID,siblingID]containsObject:identity];v.parentReminder=v.isSubtask?items[0]:nil;[items addObject:v];}moveLists=@[list];moveReminders=items;list.graphReminders=items;fixture=items[1];fixtureOrder=[NSOrderedSet orderedSetWithArray:[items valueForKey:@"remObjectID"]];
 NSData *scope=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"list_id":listID} options:0 error:NULL];NSDictionary*(^graph)(void)=^NSDictionary*{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersListOrder(scope) options:0 error:NULL];};
 NSMutableDictionary *r=[@{@"source_id":source,@"list_id":listID,@"id":leafID,@"parent_id":parentID,@"anchor_id":NSNull.null,@"placement":@"end",@"source_nodes":graph()[@"nodes"],@"ordered_ids":graph()[@"ordered_ids"]}mutableCopy];__block int gates=0;NSDictionary*(^call)(BOOL)=^NSDictionary*(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReparentLeaf([NSJSONSerialization dataWithJSONObject:r options:0 error:NULL],^{gates++;return allow;}) options:0 error:NULL];};
 NSDictionary *closed=call(NO);NSCAssert([closed[@"category"]isEqual:@"native_dispatch_cancelled"] && flagSaves==0,@"closed leaf-parent gate never saves: %@",closed);NSDictionary *good=call(YES);NSCAssert(good[@"result"] && flagSaves==1,@"one existing leaf parent/order gated save: %@",good);int prior=gates;
 extraEffect=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"content field refused");extraEffect=NO;wrongParent=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"unexpected parent refused");wrongParent=NO;wrongOrder=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"other order changes refused");wrongOrder=NO;extraTracked=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"extra task refused");extraTracked=NO;wrongTracked=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"wrong wrapper refused");wrongTracked=NO;
 list.isShared=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"shared source refused");list.isShared=NO;r[@"anchor_id"]=siblingID;r[@"placement"]=@"before";NSCAssert(call(YES)[@"error"] && gates==prior,@"anchor in other family refused");r[@"anchor_id"]=NSNull.null;r[@"placement"]=@"end";r[@"parent_id"]=leafID;NSCAssert(call(YES)[@"error"] && gates==prior,@"self-parent refused");r[@"parent_id"]=parentID;
 FixtureReminder *sibling=items[2];sibling.parentReminder=fixture;r[@"source_nodes"]=graph()[@"nodes"];NSCAssert(call(YES)[@"error"] && gates==prior,@"descendant source refused");sibling.parentReminder=items[0];r[@"source_nodes"]=graph()[@"nodes"];
 saveFails=YES;NSCAssert([call(YES)[@"mutation_dispatched"]isEqual:@YES] && flagSaves==2,@"failed parent save remains uncertain");saveFails=NO;
 r[@"parent_id"]=NSNull.null;r[@"anchor_id"]=parentID;r[@"placement"]=@"before";NSCAssert(call(YES)[@"result"] && flagSaves==3,@"existing child promotion to root");
 r[@"parent_id"]=oldID;r[@"anchor_id"]=siblingID;r[@"placement"]=@"after";NSCAssert(call(YES)[@"result"] && flagSaves==4,@"same-root-parent sibling order uses one gated save");int siblingGates=gates;r[@"placement"]=@"before";NSCAssert(call(YES)[@"error"] && gates==siblingGates,@"same-parent unchanged order refuses before save gate");
 fixture.parentReminder=items[3];fixtureOrder=[NSOrderedSet orderedSetWithArray:@[[items[0]remObjectID],[items[2]remObjectID],[items[3]remObjectID],[items[1]remObjectID]]];r[@"parent_id"]=oldID;r[@"anchor_id"]=siblingID;r[@"placement"]=@"before";r[@"source_nodes"]=graph()[@"nodes"];r[@"ordered_ids"]=graph()[@"ordered_ids"];NSCAssert(call(YES)[@"result"] && flagSaves==5,@"exact sibling anchor restores original family placement");
}}
'''
def run():
 fixture=order_fixture().replace('@interface FixtureStorage:NSObject','@interface FixtureStorage:NSObject\n@property FixtureID *accountID;')
 a=fixture.index('-(void)insertReminderChangeItem:',fixture.index('@implementation FixtureListChange'));b=fixture.index('\n@end',a);fixture=fixture[:a]+METHOD+fixture[b:]
 extra='''\n@interface FixtureParentAccount:NSObject\n@property NSSet *changedKeys;\n@end\n@implementation FixtureParentAccount @end\n''';a=fixture.index('@implementation FixtureSave');fixture=fixture[:a]+extra+fixture[a:]
 fixture=fixture.replace('@interface FixtureSave:NSObject','@interface FixtureSave:NSObject\n@property id parentAccountChange;\n-(id)trackedAccountChangeItems;\n-(id)trackedListSectionChangeItems;\n-(id)trackedSmartListChangeItems;\n-(id)trackedSmartListSectionChangeItems;\n-(id)trackedTemplateChangeItems;\n-(id)trackedTemplateSectionChangeItems;')
 fixture=fixture.replace('@implementation FixtureSave\n','''@implementation FixtureSave
-(id)trackedAccountChangeItems{FixtureParentAccount *c=self.parentAccountChange;if(!c){c=[FixtureParentAccount new];c.changedKeys=[NSSet set];self.parentAccountChange=c;}FixtureTracked *w=[FixtureTracked new];w.valueWithoutPerformingCopy=c;return @{fixture.account.remObjectID:w};}
-(id)trackedListSectionChangeItems{return @{};}
-(id)trackedSmartListChangeItems{return @{};}
-(id)trackedSmartListSectionChangeItems{return @{};}
-(id)trackedTemplateChangeItems{return @{};}
-(id)trackedTemplateSectionChangeItems{return @{};}
''',1)
 with tempfile.TemporaryDirectory(prefix='native-reminder-parent-') as directory:
  p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(shipped_source()+'\n'+fixture+'\n'+MAIN)
  r=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr
  r=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert r.returncode==0,r.stderr
 return {'status':'passed','apple_store_opened':False,'leaf_parent_promotion_and_exact_sibling_anchor_single_save':True,'closed_gate_changed_content_parents_order_wrappers_and_descendants_refused':True,'failed_save_retains_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
