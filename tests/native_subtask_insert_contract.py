"""Exercise the fixed no-context subtask alternative without opening Apple stores."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_subtree_contract import fixture_source,shipped_source,ROOT
ADDITIONS=r'''
static BOOL wrongChildAccount=NO,changedParent=NO,extraListEffect=NO;
'''
METHODS=r'''
-(id)trackedListChangeItems{FixtureTracked *wrapper=[FixtureTracked new];wrapper.valueWithoutPerformingCopy=self.moveList;return self.moveList?@{self.moveList.destination.remObjectID:wrapper}:@{};}
-(id)addReminderWithTitle:(id)title toReminderSubtaskContextChangeItem:(id)context reminderObjectID:(id)identity{return nil;}
-(id)addReminderWithTitle:(id)title toListChangeItem:(FixtureListChange *)list reminderObjectID:(FixtureID *)identity{
 FixtureReminderChange *value=[FixtureReminderChange new];value.storage=[FixtureMoveStorage new];value.storage.listID=list.destination.remObjectID;value.storage.objectID=identity;value.storage.accountID=wrongChildAccount?Identity(@"REMCDAccount",@"AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"):fixture.account.remObjectID;value.storage.titleAsString=title;value.changedKeys=[NSSet setWithArray:@[@"accountID",@"listID",@"objectID",@"titleDocumentData",@"completed"]];self.moveChanges[identity.uuid.UUIDString]=value;
 if(changedParent)((FixtureReminderChange *)self.moveChanges[fixture.remObjectID.uuid.UUIDString]).changedKeys=[NSSet setWithObject:@"titleDocumentData"];
 return value;
}
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*list=@"22222222-2222-4222-8222-222222222222",*task=@"33333333-3333-4333-8333-333333333333",*child=@"44444444-4444-4444-8444-444444444444";
 fixture=[FixtureReminder new];fixture.remObjectID=Identity(@"REMCDReminder",task);fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);fixture.list=[FixtureList new];fixture.list.remObjectID=Identity(@"REMCDList",list);fixture.list.account=fixture.account;fixture.list.storage=[FixtureListStorage new];fixture.list.storage.accountID=fixture.account.remObjectID;fixture.list.graphReminders=@[fixture];moveLists=@[fixture.list];moveReminders=@[fixture];
 NSDictionary *request=@{@"id":task,@"source_id":source,@"list_id":list,@"child_id":child,@"title":@"Synthetic first child",@"expected_children":@[]};NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];__block int gates=0;
 NSDictionary *(^call)(BOOL)=^NSDictionary *(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersCreateSubtask(data,^{gates++;return allow;}) options:0 error:NULL];};
 NSDictionary *closed=call(NO);NSCAssert([closed[@"category"] isEqual:@"native_dispatch_cancelled"] && flagSaves==0 && gates==1,@"closed gate after fully staged fallback: %@",closed);
 NSDictionary *good=call(YES);NSCAssert([good[@"result"][@"id"] isEqual:child] && [good[@"result"][@"parent_id"] isEqual:task] && flagSaves==1 && gates==2,@"one exact first child attached to exact parent in one save: %@",good);
 int before=gates;fixture.isSubtask=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"unsupported nested creation refused before dispatch");fixture.isSubtask=NO;
 wrongChildAccount=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"foreign staged child refused");wrongChildAccount=NO;
 wrongParent=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"wrong parent relation refused");wrongParent=NO;
 extraTracked=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"unexpected reminder tracked refused");extraTracked=NO;
 wrongTracked=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"wrapper cannot substitute different change object");wrongTracked=NO;
 extraEffect=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"unexpected child content effect refused");extraEffect=NO;
 changedParent=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"existing parent content change refused");changedParent=NO;
 extraListEffect=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"list rename cannot ride child insertion");extraListEffect=NO;
 childCollision=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"existing or deleted ID cannot be reused");childCollision=NO;
 fixture.list.graphReminders=@[];NSCAssert(call(YES)[@"error"] && gates==before,@"unknown child inventory refused");fixture.list.graphReminders=@[fixture];
 saveFails=YES;NSCAssert([call(YES)[@"mutation_dispatched"] isEqual:@YES] && flagSaves==2,@"failed native save remains uncertain");
}}
'''

def run():
    fixture=fixture_source()
    fixture=fixture.replace('@interface FixtureMoveStorage:NSObject',ADDITIONS+'\n@interface FixtureMoveStorage:NSObject\n@property FixtureID *objectID;\n@property FixtureID *accountID;\n@property NSString *titleAsString;')
    fixture=fixture.replace('@interface FixtureListChange:NSObject','@interface FixtureListChange:NSObject\n@property NSSet *changedKeys;')
    fixture=fixture.replace('@interface FixtureSave:NSObject','@interface FixtureSave:NSObject\n@property FixtureListChange *moveList;\n-(id)trackedListChangeItems;\n-(id)addReminderWithTitle:(id)title toListChangeItem:(id)list reminderObjectID:(id)identity;')
    fixture=fixture.replace('v.destination=list;return v;', 'v.destination=list;self.moveList=v;return v;')
    fixture=fixture.replace('@implementation FixtureSave','@implementation FixtureSave\n'+METHODS)
    old='item.changedKeys=[NSSet setWithArray:extraEffect?@[@"listID",@"notes"]:parent?@[@"listID",@"parentReminderID"]:@[@"listID"]];}'
    new='NSSet *relationKeys=[NSSet setWithArray:extraEffect?@[@"listID",@"notes"]:parent?@[@"listID",@"parentReminderID"]:@[@"listID"]];item.changedKeys=[(item.changedKeys?:[NSSet set]) setByAddingObjectsFromSet:relationKeys];self.changedKeys=[NSSet setWithArray:extraListEffect?@[@"name"]:@[@"reminderIDsMergeableOrdering",@"reminderIDsOrderingHints"]];}'
    assert old in fixture;fixture=fixture.replace(old,new)
    with tempfile.TemporaryDirectory(prefix='native-subtask-insert-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(shipped_source()+'\n'+fixture+'\n'+MAIN)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
    return {'status':'passed','apple_store_opened':False,'exact_no_context_child_staging':True,'closed_gate_and_unreviewed_side_effects_refused':True,'one_save_and_failed_save_uncertainty_verified':True}
if __name__=='__main__':print(json.dumps(run()))
