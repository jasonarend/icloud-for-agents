"""Exercise the shipped section-delta writer with synthetic native objects only."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <math.h>
static BOOL extraKeys=NO,extraTask=NO,badWrapper=NO,shared=NO,saveFails=NO;
static int saves=0;
static NSDictionary *graph,*inventory,*section;
static id leaf,listObject,accountChange;
static NSData *EncodeFlag(id value){return [NSJSONSerialization dataWithJSONObject:value options:0 error:NULL];}
static NSDictionary *FlagFailure(NSString *category){return @{ @"protocol_version":@1,@"error":@"refused",@"category":category };}
static id ObjectValue(id value,NSString *key){return [value valueForKey:key];}
static NSString *ObjectUUID(id value,NSString *entity){return [value valueForKey:@"identifier"];}
static BOOL UUIDEqual(id a,id b){return [a isKindOfClass:NSString.class] && [b isKindOfClass:NSString.class] && [a caseInsensitiveCompare:b]==NSOrderedSame;}
static BOOL MethodMatches(Class cls,NSString *selector,NSString *expected,BOOL classMethod){
 Method m=classMethod?class_getClassMethod(cls,NSSelectorFromString(selector)):class_getInstanceMethod(cls,NSSelectorFromString(selector));if(!m)return NO;
 NSMutableString *s=[NSMutableString string];char *ret=method_copyReturnType(m);[s appendFormat:@"%s",ret];free(ret);for(unsigned i=0;i<method_getNumberOfArguments(m);i++){char *a=method_copyArgumentType(m,i);[s appendFormat:@"%s",a];free(a);}return [s isEqual:expected];
}
@interface FixtureObject:NSObject
@property NSString *identifier;
@property id list,parentReminder;
@end
@implementation FixtureObject @end
static id SelectedReminderWithStore(NSData *data,NSDictionary **failure,id *store){*store=[NSObject new];return leaf;}
static BOOL LifecycleListPrivate(id list,NSString *source,NSString *identifier){return !shared && list==listObject;}
static BOOL LifecycleGraphMatches(id value,id nodes,id source,id list){return [value[@"nodes"]isEqual:nodes];}
static NSData *ICloudRemindersListOrder(NSData *data){return EncodeFlag(graph);}
static NSData *ICloudRemindersListSections(NSData *data){return EncodeFlag(inventory);}
static NSData *ICloudRemindersReadSection(NSData *data){return EncodeFlag(@{@"native_metadata":section});}
@interface REMMembership:NSObject
@property(readonly) NSUUID *memberIdentifier,*groupIdentifier;
@property(readonly) NSDate *modifiedOn;
@property(readonly) BOOL isObsolete;
-(id)initWithMemberIdentifier:(NSUUID*)member groupIdentifier:(NSUUID*)group isObsolete:(BOOL)obsolete modifiedOn:(NSDate*)date;
@end
@implementation REMMembership
-(id)initWithMemberIdentifier:(NSUUID*)member groupIdentifier:(NSUUID*)group isObsolete:(BOOL)obsolete modifiedOn:(NSDate*)date{if((self=[super init])){_memberIdentifier=member;_groupIdentifier=group;_isObsolete=obsolete;_modifiedOn=date;}return self;}
@end
@interface REMMemberships:NSObject
@property NSDictionary *membershipByMemberIdentifier;
-(id)initWithMembershipByMemberIdentifier:(NSDictionary*)values;
-(id)groupIdentifierOfMemberWithIdentifier:(id)member;
@end
@implementation REMMemberships
-(id)initWithMembershipByMemberIdentifier:(NSDictionary*)values{if((self=[super init]))self.membershipByMemberIdentifier=values;return self;}
-(id)groupIdentifierOfMemberWithIdentifier:(id)member{return [self.membershipByMemberIdentifier[member]groupIdentifier];}
@end
@interface FixtureChange:NSObject
@property NSSet *changedKeys;
@property id sectionsContextChangeItem,unsavedMembershipsOfRemindersInSections;
@end
@implementation FixtureChange
-(void)setUnsavedMembershipsOfRemindersInSections:(id)value{_unsavedMembershipsOfRemindersInSections=value;self.changedKeys=[NSSet setWithArray:extraKeys?@[@"unsavedMembershipsOfRemindersInSections",@"notes"]:@[@"unsavedMembershipsOfRemindersInSections"]];}
@end
@interface REMSaveRequest:NSObject
@property FixtureChange *change;
-(id)initWithStore:(id)store;
-(id)updateList:(id)list;
-(BOOL)saveSynchronouslyWithError:(NSError**)error;
-(id)trackedAccountChangeItems;
-(id)trackedReminderChangeItems;
-(id)trackedListSectionChangeItems;
-(id)trackedSmartListChangeItems;
-(id)trackedSmartListSectionChangeItems;
-(id)trackedTemplateChangeItems;
-(id)trackedTemplateSectionChangeItems;
@end
@implementation REMSaveRequest
-(id)initWithStore:(id)store{return [super init];}
-(id)updateList:(id)list{self.change=[FixtureChange new];self.change.sectionsContextChangeItem=self.change;return self.change;}
-(BOOL)saveSynchronouslyWithError:(NSError**)error{saves++;return !saveFails;}
-(id)trackedAccountChangeItems{return @{@"account":@{@"valueWithoutPerformingCopy":accountChange}};}
-(id)trackedReminderChangeItems{return extraTask?@{@"unreviewed":@1}:@{};}
-(id)trackedListSectionChangeItems{return @{};}
-(id)trackedSmartListChangeItems{return @{};}
-(id)trackedSmartListSectionChangeItems{return @{};}
-(id)trackedTemplateChangeItems{return @{};}
-(id)trackedTemplateSectionChangeItems{return @{};}
@end
static BOOL LifecycleTrackedList(id save,id change,id list,id keys){return !badWrapper && [ObjectValue(change,@"changedKeys")isEqual:keys];}
static BOOL TrackedGroupObject(id save,id key,id entity,id source,id expected){return expected==accountChange;}
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*list=@"22222222-2222-4222-8222-222222222222",*identifier=@"33333333-3333-4333-8333-333333333333",*a=@"44444444-4444-4444-8444-444444444444",*b=@"55555555-5555-4555-8555-555555555555";
 FixtureObject *reminder=[FixtureObject new];reminder.identifier=identifier;listObject=[NSObject new];reminder.list=listObject;leaf=reminder;FixtureChange *account=[FixtureChange new];account.changedKeys=[NSSet set];accountChange=account;
 graph=@{@"nodes":@[@{@"id":identifier,@"parent_id":NSNull.null}],@"ordered_ids":@[identifier]};inventory=@{@"sections":@[@{@"id":a,@"title":@"Synthetic A"},@{@"id":b,@"title":@"Synthetic B"}]};section=@{@"section":@{@"id":a,@"title":@"Synthetic A"}};
 NSMutableDictionary *r=[@{@"source_id":source,@"list_id":list,@"id":identifier,@"section_id":b,@"source_nodes":graph[@"nodes"],@"ordered_ids":graph[@"ordered_ids"],@"before_sections":inventory,@"before_section":section,@"previous_modified_on":@100}mutableCopy];
 __block int gates=0;NSDictionary*(^call)(BOOL)=^NSDictionary*(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersSetSection(EncodeFlag(r),^{gates++;return allow;})options:0 error:NULL];};
 NSCAssert([call(NO)[@"category"]isEqual:@"native_dispatch_cancelled"] && gates==1 && saves==0,@"closed gate stages typed delta and never saves");NSCAssert(call(YES)[@"result"] && gates==2 && saves==1,@"one gated save");int prior=gates;
 extraKeys=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"content side effect refused");extraKeys=NO;extraTask=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"tracked reminder refused");extraTask=NO;badWrapper=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"wrong list tracking refused");badWrapper=NO;shared=YES;NSCAssert(call(YES)[@"error"] && gates==prior,@"shared list refused");shared=NO;
 r[@"section_id"]=a;NSCAssert(call(YES)[@"error"] && gates==prior,@"no-op refused");r[@"section_id"]=list;NSCAssert(call(YES)[@"error"] && gates==prior,@"foreign section refused");r[@"section_id"]=b;r[@"previous_modified_on"]=@1e20;NSCAssert(call(YES)[@"error"] && gates==prior,@"future old timestamp refused");r[@"previous_modified_on"]=@100;
 r[@"before_section"]=@{};NSCAssert(call(YES)[@"error"] && gates==prior,@"stale source section refused");r[@"before_section"]=section;
 r[@"section_id"]=NSNull.null;NSCAssert(call(YES)[@"result"] && saves==2,@"remove membership uses explicit nil group");saveFails=YES;NSCAssert([call(YES)[@"mutation_dispatched"]isEqual:@YES] && saves==3,@"save error preserves dispatched uncertainty");
}}
'''
def run():
 source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();start=source.index('NSData *ICloudRemindersSetSection(');operation=source[start:source.index('\n}\n',start)+3]
 with tempfile.TemporaryDirectory(prefix='native-reminder-sections-') as directory:
  root=Path(directory);(root/'test.m').write_text(FIXTURE+'\n'+operation+'\n'+MAIN)
  result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(root/'test.m'),'-o',str(root/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
  result=subprocess.run([str(root/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
 return {'status':'passed','apple_store_opened':False,'typed_single_member_delta_and_nil_group':True,'closed_gate_side_effect_tracking_foreign_noop_stale_and_future_refusals':True,'failed_save_retains_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
