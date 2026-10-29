"""Exercise shipped native root ordering with synthetic storage and no Apple data."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_subtree_contract import fixture_source,shipped_source,ROOT
EXTRA=r'''
static NSOrderedSet *fixtureOrder;
static BOOL wrongOrder=NO;
@interface FixtureOrderStorage:NSObject
@property NSOrderedSet *reminderIDsMergeableOrdering;
@end
@implementation FixtureOrderStorage
@end
'''
METHODS=r'''
-(void)stageRoot:(FixtureReminderChange *)root anchor:(FixtureReminderChange *)anchor after:(BOOL)after{
 NSMutableArray *values=[fixtureOrder.array mutableCopy];NSString *rootID=nil,*anchorID=nil;for(NSString *key in activeChanges){if(activeChanges[key]==root)rootID=key;if(activeChanges[key]==anchor)anchorID=key;}
 id rootObject=nil,anchorObject=nil;for(FixtureID *value in values){if([value.uuid.UUIDString isEqual:rootID])rootObject=value;if([value.uuid.UUIDString isEqual:anchorID])anchorObject=value;}
 [values removeObject:rootObject];NSUInteger at=[values indexOfObject:anchorObject]+(after?1:0);[values insertObject:rootObject atIndex:at];if(wrongOrder)[values exchangeObjectAtIndex:0 withObjectAtIndex:1];self.storage.reminderIDsMergeableOrdering=[NSOrderedSet orderedSetWithArray:values];root.changedKeys=[NSSet setWithArray:extraEffect?@[@"listID",@"notes"]:@[@"listID"]];
 self.changedKeys=[NSSet setWithArray:@[@"reminderIDsMergeableOrdering",@"reminderIDsOrderingHints"]];if(wrongParent)root.storage.parentReminderID=Identity(@"REMCDReminder",anchorID);
}
-(void)insertReminderChangeItem:(FixtureReminderChange *)root beforeReminderChangeItem:(FixtureReminderChange *)anchor{[self stageRoot:root anchor:anchor after:NO];}
-(void)insertReminderChangeItem:(FixtureReminderChange *)root afterReminderChangeItem:(FixtureReminderChange *)anchor{[self stageRoot:root anchor:anchor after:YES];}
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*listID=@"22222222-2222-4222-8222-222222222222",*rootID=@"44444444-4444-4444-8444-444444444444",*childID=@"55555555-5555-4555-8555-555555555555",*anchorID=@"66666666-6666-4666-8666-666666666666";
 FixtureAccount *account=[FixtureAccount new];account.remObjectID=Identity(@"REMCDAccount",source);FixtureList *list=[FixtureList new];list.remObjectID=Identity(@"REMCDList",listID);list.account=account;list.storage=[FixtureListStorage new];list.storage.accountID=account.remObjectID;
 NSMutableArray *items=[NSMutableArray array];for(NSString *identifier in @[rootID,childID,anchorID]){FixtureReminder *value=[FixtureReminder new];value.remObjectID=Identity(@"REMCDReminder",identifier);value.list=list;value.account=account;value.isSubtask=[identifier isEqual:childID];value.parentReminder=value.isSubtask?items[0]:nil;[items addObject:value];}moveLists=@[list];moveReminders=items;fixture=items[0];list.graphReminders=items;
 fixtureOrder=[NSOrderedSet orderedSetWithArray:@[[items[2] remObjectID],[items[0] remObjectID],[items[1] remObjectID]]];
 NSData *scope=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"list_id":listID} options:0 error:NULL];NSDictionary *observed=[NSJSONSerialization JSONObjectWithData:ICloudRemindersListOrder(scope) options:0 error:NULL];NSCAssert(([observed[@"ordered_ids"]isEqual:@[anchorID,rootID,childID]]),@"exact selected native order: %@",observed);
 NSMutableDictionary *request=[@{@"source_id":source,@"list_id":listID,@"id":rootID,@"anchor_id":anchorID,@"placement":@"before",@"source_nodes":observed[@"nodes"],@"ordered_ids":observed[@"ordered_ids"]}mutableCopy];__block int gates=0;
 NSDictionary *(^call)(BOOL)=^NSDictionary*(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReorderRoot([NSJSONSerialization dataWithJSONObject:request options:0 error:NULL],^{gates++;return allow;}) options:0 error:NULL];};
 NSDictionary *closed=call(NO);NSCAssert([closed[@"category"]isEqual:@"native_dispatch_cancelled"] && gates==1 && flagSaves==0,@"closed gate never saves: %@",closed);
 NSDictionary *good=call(YES);NSCAssert([good[@"result"][@"id"]isEqual:rootID] && gates==2 && flagSaves==1,@"exact order saves once: %@",good);int before=gates;
 wrongOrder=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"wrong staged order refused");wrongOrder=NO;
 extraEffect=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"unexpected content change refused");extraEffect=NO;
 wrongParent=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"changed parent refused");wrongParent=NO;
 extraTracked=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"extra reminder refused");extraTracked=NO;
 wrongTracked=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"wrong tracked wrapper refused");wrongTracked=NO;
 list.isShared=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"shared list refused");list.isShared=NO;
 request[@"anchor_id"]=childID;NSCAssert(call(YES)[@"error"] && gates==before,@"subtask anchor refused");request[@"anchor_id"]=anchorID;
 request[@"ordered_ids"]=@[rootID,anchorID,childID];NSCAssert(call(YES)[@"error"] && gates==before,@"changed order refused");request[@"ordered_ids"]=observed[@"ordered_ids"];
 saveFails=YES;NSCAssert([call(YES)[@"mutation_dispatched"]isEqual:@YES] && flagSaves==2,@"failed save retains uncertainty");saveFails=NO;
 request[@"placement"]=@"after";NSCAssert(call(YES)[@"error"] && flagSaves==2,@"no-op refused");
 fixtureOrder=[NSOrderedSet orderedSetWithArray:@[[items[0]remObjectID],[items[2]remObjectID],[items[1]remObjectID]]];request[@"ordered_ids"]=@[rootID,anchorID,childID];NSCAssert(call(YES)[@"result"] && flagSaves==3,@"reverse root relative move saves once");
}}
'''
def order_fixture():
    fixture=fixture_source().replace('@property FixtureID *parentReminderID;','@property FixtureID *parentReminderID;\n@property FixtureID *accountID;')
    fixture=fixture.replace('@interface FixtureListChange:NSObject',EXTRA+'\n@interface FixtureListChange:NSObject\n@property FixtureOrderStorage *storage;\n@property NSSet *changedKeys;\n-(void)insertReminderChangeItem:(id)root beforeReminderChangeItem:(id)anchor;\n-(void)insertReminderChangeItem:(id)root afterReminderChangeItem:(id)anchor;')
    fixture=fixture.replace('@interface FixtureList:NSObject','@interface FixtureList:NSObject\n-(id)reminderIDsOrdering;')
    fixture=fixture.replace('@implementation FixtureList\n','@implementation FixtureList\n-(id)reminderIDsOrdering{return fixtureOrder;}\n')
    # The order global is needed before the list implementation; its storage class follows later.
    fixture=fixture.replace('static NSOrderedSet *fixtureOrder;','');fixture='static NSOrderedSet *fixtureOrder;\n'+fixture
    fixture=fixture.replace('@implementation FixtureListChange','@implementation FixtureListChange\n'+METHODS)
    fixture=fixture.replace('@interface FixtureSave:NSObject','@interface FixtureSave:NSObject\n@property FixtureListChange *orderChange;\n-(id)trackedListChangeItems;')
    fixture=fixture.replace('v.destination=list;return v;','v.destination=list;v.storage=[FixtureOrderStorage new];self.orderChange=v;return v;')
    fixture=fixture.replace('v.storage.listID=[reminder list].remObjectID;','v.storage.accountID=[[reminder account] remObjectID];v.storage.listID=[reminder list].remObjectID;')
    fixture=fixture.replace('@implementation FixtureSave','@implementation FixtureSave\n-(id)trackedListChangeItems{FixtureTracked *v=[FixtureTracked new];v.valueWithoutPerformingCopy=self.orderChange;return @{fixture.list.remObjectID:v};}')
    return fixture

def run():
    fixture=order_fixture()
    with tempfile.TemporaryDirectory(prefix='native-reminder-order-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(shipped_source()+'\n'+fixture+'\n'+MAIN)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
    return {'status':'passed','apple_store_opened':False,'exact_root_order_single_save':True,'closed_gate_stale_order_changed_hierarchy_and_unreviewed_fields_refused':True,'failed_save_preserves_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
