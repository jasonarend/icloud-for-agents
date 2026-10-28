"""Execute shipped subtree staging with synthetic stores; never open Apple data."""
import json, subprocess, tempfile
from pathlib import Path
from native_reminderkit_flags_contract import FIXTURE
ROOT=Path(__file__).resolve().parents[1]

EXTRA=r'''
@interface FixtureTracked:NSObject
@property id valueWithoutPerformingCopy;
@end
@implementation FixtureTracked
@end
static BOOL wrongParent=NO,extraEffect=NO,extraTracked=NO,wrongTracked=NO;
@interface FixtureMoveStorage:NSObject
@property FixtureID *listID;
@property FixtureID *parentReminderID;
@end
@implementation FixtureMoveStorage
@end
'''
SAVE=r'''
@implementation FixtureSave
-(id)initWithStore:(id)store{self=[super init];self.moveChanges=[NSMutableDictionary dictionary];return self;}
-(id)updateList:(id)list{FixtureListChange *v=[FixtureListChange new];v.destination=list;return v;}
-(id)updateReminder:(id)reminder{NSString *key=[reminder remObjectID].uuid.UUIDString;FixtureReminderChange *v=self.moveChanges[key];if(!v){v=[FixtureReminderChange new];v.storage=[FixtureMoveStorage new];v.storage.listID=[reminder list].remObjectID;v.storage.parentReminderID=[reminder parentReminder].remObjectID;v.changedKeys=[NSSet set];self.moveChanges[key]=v;}return v;}
-(id)trackedReminderChangeItems{NSMutableDictionary *v=[NSMutableDictionary dictionary];for(NSString *key in self.moveChanges){FixtureTracked *wrapper=[FixtureTracked new];wrapper.valueWithoutPerformingCopy=wrongTracked?[NSObject new]:self.moveChanges[key];v[Identity(@"REMCDReminder",key)]=wrapper;}if(extraTracked){FixtureTracked *wrapper=[FixtureTracked new];wrapper.valueWithoutPerformingCopy=[NSObject new];v[Identity(@"REMCDReminder",@"AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA")]=wrapper;}return v;}
-(BOOL)saveSynchronouslyWithError:(NSError **)error{flagSaves++;if(saveFails){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:nil];return NO;}return YES;}
@end
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*origin=@"22222222-2222-4222-8222-222222222222",*destination=@"33333333-3333-4333-8333-333333333333";
 NSArray *ids=@[@"44444444-4444-4444-8444-444444444444",@"55555555-5555-4555-8555-555555555555",@"66666666-6666-4666-8666-666666666666"];
 FixtureAccount *account=[FixtureAccount new];account.remObjectID=Identity(@"REMCDAccount",source);NSMutableArray *lists=[NSMutableArray array];for(NSString *identity in @[origin,destination]){FixtureList *list=[FixtureList new];list.remObjectID=Identity(@"REMCDList",identity);list.account=account;list.storage=[FixtureListStorage new];list.storage.accountID=account.remObjectID;list.graphReminders=@[];[lists addObject:list];}moveLists=lists;
 NSMutableArray *reminders=[NSMutableArray array],*nodes=[NSMutableArray array];for(NSUInteger i=0;i<ids.count;i++){FixtureReminder *value=[FixtureReminder new];value.remObjectID=Identity(@"REMCDReminder",ids[i]);value.account=account;value.list=lists[0];value.isSubtask=i>0;value.parentReminder=i>0?reminders[i-1]:nil;value.subtaskContext=[FixtureHierarchy new];value.subtaskContext.children=@[];[reminders addObject:value];[nodes addObject:@{@"id":ids[i],@"parent_id":i>0?ids[i-1]:(id)[NSNull null]}];}
 for(NSUInteger i=0;i+1<reminders.count;i++)((FixtureHierarchy *)[reminders[i] subtaskContext]).children=@[reminders[i+1]];
 moveReminders=reminders;fixture=reminders[0];((FixtureList *)lists[0]).graphReminders=reminders;
 NSMutableDictionary *req=[@{@"id":ids[0],@"source_id":source,@"list_id":origin,@"destination_list_id":destination,@"source_nodes":nodes,@"destination_nodes":@[],@"moved_ids":ids} mutableCopy];
 __block int gates=0;NSDictionary *(^call)(BOOL)=^NSDictionary *(BOOL allow){NSData *data=[NSJSONSerialization dataWithJSONObject:req options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersMoveSubtree(data,^{gates++;return allow;}) options:0 error:NULL];};
 NSDictionary *closed=call(NO);NSCAssert([closed[@"category"] isEqual:@"native_dispatch_cancelled"] && gates==1 && flagSaves==0,@"closed gate traverses/stages every descendant but never saves: %@",closed);
 NSDictionary *good=call(YES);NSCAssert([good[@"result"][@"moved_ids"] isEqual:ids] && flagSaves==1 && gates==2,@"one reviewed root, child and grandchild use one gated save");
 int prior=gates;
 extraEffect=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"unreviewed staged content keys never reach the gate");extraEffect=NO;
 wrongParent=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"changed child parent refused");wrongParent=NO;
 extraTracked=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"unreviewed tracked native object refused");extraTracked=NO;
 wrongTracked=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"tracked wrapper must bind exact staged object");wrongTracked=NO;
 req[@"moved_ids"]=@[ids[0]];NSCAssert(call(YES)[@"error"] && gates==prior,@"partial subtree refused");req[@"moved_ids"]=ids;
 req[@"destination_nodes"]=@[@{@"id":ids[0],@"parent_id":[NSNull null]}];NSCAssert(call(YES)[@"error"] && gates==prior,@"stale destination membership refused");req[@"destination_nodes"]=@[];
 ((FixtureList *)lists[1]).isShared=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"shared destination refused");((FixtureList *)lists[1]).isShared=NO;
 ((FixtureHierarchy *)[reminders[0] subtaskContext]).children=@[];NSCAssert(call(YES)[@"error"] && gates==prior,@"provider moving traversal must include every child");((FixtureHierarchy *)[reminders[0] subtaskContext]).children=@[reminders[1]];
 saveFails=YES;NSDictionary *failed=call(YES);NSCAssert([failed[@"mutation_dispatched"] isEqual:@YES] && flagSaves==2,@"native save failure remains uncertain after dispatch");
}}
'''

def run():
    fixture=FIXTURE[:FIXTURE.index('int main()')]
    fixture=fixture.replace('@interface FixtureID:NSObject','@interface FixtureID:NSObject<NSCopying>').replace('@implementation FixtureID','@implementation FixtureID\n-(id)copyWithZone:(NSZone *)zone{return self;}')
    fixture=fixture.replace('@interface FixtureStore:NSObject','static NSArray *moveLists,*moveReminders;\n@interface FixtureStore:NSObject')
    fixture=fixture.replace('return [[objectID uuid] isEqual:fixture.list.remObjectID.uuid]?fixture.list:nil;', 'for(FixtureList *v in moveLists)if([v.remObjectID.uuid isEqual:[objectID uuid]])return v;return nil;')
    fixture=fixture.replace('return [[objectID uuid] isEqual:fixture.remObjectID.uuid]?fixture:nil;', 'for(FixtureReminder *v in moveReminders)if([v.remObjectID.uuid isEqual:[objectID uuid]])return v;return nil;')
    fixture=fixture.replace('-(id)fetchRemindersWithError:(NSError **)error;', '-(id)fetchRemindersWithError:(NSError **)error;\n-(id)fetchRemindersForMovingWithError:(NSError **)error;')
    fixture=fixture.replace('@implementation FixtureHierarchy','@implementation FixtureHierarchy\n-(id)fetchRemindersForMovingWithError:(NSError **)error{return self.children;}')
    fixture=fixture.replace('@interface FixtureReminderChange:NSObject', EXTRA+'\n@interface FixtureReminderChange:NSObject\n@property FixtureMoveStorage *storage;\n@property NSSet *changedKeys;')
    fixture=fixture.replace('@interface FixtureListChange:NSObject','@interface FixtureListChange:NSObject\n@property FixtureList *destination;\n-(void)insertReminderChangeItem:(id)item adjacentToReminderChangeItem:(id)adjacent isAfter:(BOOL)after withParentReminderChangeItem:(id)parent;')
    fixture=fixture.replace('@implementation FixtureListChange', '@implementation FixtureListChange\n-(void)insertReminderChangeItem:(FixtureReminderChange *)item adjacentToReminderChangeItem:(id)adjacent isAfter:(BOOL)after withParentReminderChangeItem:(FixtureReminderChange *)parent{NSCAssert(adjacent==nil && after,@"append fixed operation");item.storage.listID=self.destination.remObjectID;item.storage.parentReminderID=parent?(wrongParent?Identity(@"REMCDReminder",@"AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"):nil):nil;for(NSString *key in activeChanges)if(activeChanges[key]==parent)item.storage.parentReminderID=wrongParent?Identity(@"REMCDReminder",@"AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"):Identity(@"REMCDReminder",key);item.changedKeys=[NSSet setWithArray:extraEffect?@[@"listID",@"notes"]:parent?@[@"listID",@"parentReminderID"]:@[@"listID"]];}')
    fixture=fixture.replace('@interface FixtureListChange:NSObject','static NSMutableDictionary *activeChanges;\nstatic FixtureID *Identity(NSString *,NSString *);\n@interface FixtureListChange:NSObject')
    fixture=fixture.replace('@interface FixtureSave:NSObject','@interface FixtureSave:NSObject\n@property NSMutableDictionary *moveChanges;\n-(id)trackedReminderChangeItems;')
    start=fixture.index('@implementation FixtureSave');end=fixture.index('\n@end',start)+len('\n@end')
    fixture=fixture[:start]+SAVE.replace('self.moveChanges=[NSMutableDictionary dictionary];','self.moveChanges=[NSMutableDictionary dictionary];activeChanges=self.moveChanges;')+fixture[end:]
    source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text().replace('    if(NSProcessInfo.processInfo.operatingSystemVersion.majorVersion!=27)return NO;','').replace('    if(!dlopen("/System/Library/PrivateFrameworks/ReminderKit.framework/ReminderKit",RTLD_LAZY|RTLD_LOCAL))return NO;','')
    source=source.replace('NSProcessInfo.processInfo.operatingSystemVersion.majorVersion!=27 || !dlopen("/System/Library/PrivateFrameworks/ReminderKit.framework/ReminderKit",RTLD_LAZY|RTLD_LOCAL)','NO')
    for real,fake in [('REMAssignment','FixtureAssignment'),('REMDueDateDeltaInterval','FixtureDelta'),('REMStore','FixtureStore'),('REMListsDataView','FixtureListsView'),('REMObjectID','FixtureID'),('REMReminder','FixtureReminder'),('REMSaveRequest','FixtureSave'),('REMListSectionsDataView','FixtureSectionView')]:source=source.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
    with tempfile.TemporaryDirectory(prefix='native-subtree-contract-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(source+'\n'+fixture+'\n'+MAIN)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
    return {'status':'passed','apple_store_opened':False,'root_child_grandchild_single_save':True,'closed_gate_and_unreviewed_changes_refused':True,'native_save_failure_retains_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
