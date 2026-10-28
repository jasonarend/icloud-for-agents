"""Shipped fixed ReminderKit flag/tag reads against synthetic objects, without Apple stores."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
@interface FixtureID:NSObject
@property NSUUID *uuid;
@property NSString *entityName;
+(id)objectIDWithURL:(NSURL *)url;
@end
@implementation FixtureID
+(id)objectIDWithURL:(NSURL *)url{FixtureID *v=[FixtureID new];v.uuid=[[NSUUID alloc]initWithUUIDString:url.lastPathComponent];v.entityName=url.host;return v;}
@end
@interface FixtureAccount:NSObject
@property FixtureID *remObjectID;
@end
@implementation FixtureAccount
@end
@interface FixtureList:NSObject
@property FixtureID *remObjectID;
@property FixtureAccount *account;
@end
@implementation FixtureList
@end
@interface FixtureFlag:NSObject
@property NSInteger flagged;
@end
@implementation FixtureFlag
@end
@interface FixtureUrgent:NSObject
@property BOOL enabled;
-(BOOL)isUrgentStateEnabledForCurrentUser;
@end
@implementation FixtureUrgent
-(BOOL)isUrgentStateEnabledForCurrentUser{return self.enabled;}
@end
@interface FixtureTags:NSObject
@property id hashtags;
@end
@implementation FixtureTags
@end
@interface FixtureTag:NSObject
@property FixtureID *remObjectID;
@property FixtureID *accountID;
@property FixtureID *reminderID;
@property NSString *name;
@property NSInteger type;
@end
@implementation FixtureTag
@end
@interface FixtureHierarchy:NSObject
@property BOOL hasChildren;
@property BOOL denied;
@property id children;
-(BOOL)hasSubtasksWithError:(NSError **)error;
-(id)fetchRemindersWithError:(NSError **)error;
@end
@implementation FixtureHierarchy
-(BOOL)hasSubtasksWithError:(NSError **)error {if(self.denied){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:nil];return NO;}return self.hasChildren;}
-(id)fetchRemindersWithError:(NSError **)error {if(self.denied){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:nil];return nil;}return self.children;}
@end
@interface FixtureStorage:NSObject
@property BOOL hasUnfetchedDueDateDeltaAlerts;
@property NSData *dueDateDeltaAlertsData;
@end
@implementation FixtureStorage
@end
@interface FixtureReminder:NSObject
@property FixtureID *remObjectID;
@property FixtureAccount *account;
@property FixtureList *list;
@property FixtureStorage *storage;
@property id flaggedContext;
@property id urgentAlarmContext;
@property id hashtagContext;
@property BOOL isSubtask;
@property FixtureReminder *parentReminder;
@property FixtureHierarchy *subtaskContext;
@end
@implementation FixtureReminder
@end
static FixtureReminder *fixture;
static BOOL denied=NO;static BOOL childCollision=NO;static BOOL childLookupError=NO;
@interface FixtureSectionStorage:NSObject
@property FixtureID *accountID;@property FixtureID *listID;@property NSString *displayName;
@end
@implementation FixtureSectionStorage
@end
@interface FixtureSection:NSObject
@property FixtureID *remObjectID;@property FixtureList *list;@property FixtureSectionStorage *storage;
@end
@implementation FixtureSection
@end
static NSArray *fixtureSections;
@interface FixtureSectionView:NSObject
-(id)initWithStore:(id)store;
-(id)fetchListSectionWithReminderID:(id)identity error:(NSError **)error;
@end
@implementation FixtureSectionView
-(id)initWithStore:(id)store{return [super init];}
-(id)fetchListSectionWithReminderID:(id)identity error:(NSError **)error{if(fixtureSections.count)return fixtureSections[0];*error=[NSError errorWithDomain:@"com.apple.reminderkit" code:-3009 userInfo:nil];return nil;}
@end
@interface FixtureStore:NSObject
-(id)fetchReminderWithObjectID:(id)objectID error:(NSError **)error;
-(id)fetchReminderIncludingMarkedForDeleteWithObjectID:(id)objectID error:(NSError **)error;
-(id)fetchListWithObjectID:(id)objectID error:(NSError **)error;
-(id)fetchListSectionsWithListObjectID:(id)objectID error:(NSError **)error;
-(id)fetchListSectionWithObjectID:(id)objectID error:(NSError **)error;
@end
@implementation FixtureStore
-(id)fetchListWithObjectID:(id)objectID error:(NSError **)error{return [[objectID uuid] isEqual:fixture.list.remObjectID.uuid]?fixture.list:nil;}
-(id)fetchListSectionsWithListObjectID:(id)objectID error:(NSError **)error{return fixtureSections;}
-(id)fetchListSectionWithObjectID:(id)objectID error:(NSError **)error{*error=[NSError errorWithDomain:@"com.apple.reminderkit" code:-3000 userInfo:nil];return nil;}
-(id)fetchReminderIncludingMarkedForDeleteWithObjectID:(id)objectID error:(NSError **)error{if(childLookupError){*error=[NSError errorWithDomain:@"synthetic" code:9 userInfo:nil];return nil;}return childCollision?fixture:nil;}
-(id)fetchReminderWithObjectID:(id)objectID error:(NSError **)error{
 if(denied){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:@{NSLocalizedDescriptionKey:@"private-content-must-not-leak"}];return nil;}
 NSCAssert([[objectID entityName] isEqual:@"REMCDReminder"],@"native Core Data entity required for lookup");
 return [[objectID uuid] isEqual:fixture.remObjectID.uuid]?fixture:nil;
}
@end
static int flagSaves=0;static BOOL saveFails=NO;
@interface FixtureFlagChange:NSObject
@property NSInteger flagged;
@end
@implementation FixtureFlagChange
@end
@interface FixtureTagChange:NSObject
@property NSMutableArray *hashtags;
-(void)removeHashtag:(id)tag;
-(id)addHashtagWithType:(NSInteger)type name:(id)name;
@end
@implementation FixtureTagChange
-(void)removeHashtag:(id)tag{[self.hashtags removeObject:tag];}
-(id)addHashtagWithType:(NSInteger)type name:(id)name{FixtureTag *tag=[FixtureTag new];FixtureID *identity=[FixtureID new];identity.uuid=[[NSUUID alloc]initWithUUIDString:@"AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"];identity.entityName=@"REMCDHashtag";tag.remObjectID=identity;tag.accountID=fixture.account.remObjectID;tag.reminderID=fixture.remObjectID;tag.name=name;tag.type=type;[self.hashtags addObject:tag];return tag;}
@end
@interface FixtureUrgentChange:NSObject
@property BOOL enabled;
-(void)setIsUrgentStateEnabledForCurrentUser:(BOOL)value;
@end
@implementation FixtureUrgentChange
-(void)setIsUrgentStateEnabledForCurrentUser:(BOOL)value{self.enabled=value;}
@end
@interface FixtureDelta:NSObject
-(id)initWithUnit:(NSInteger)unit count:(NSInteger)count;
@end
@implementation FixtureDelta
-(id)initWithUnit:(NSInteger)unit count:(NSInteger)count{return [super init];}
@end
@interface FixtureEarlyChange:NSObject
-(id)addDueDateDeltaAlertWithDueDateDelta:(id)delta;
@end
@implementation FixtureEarlyChange
-(id)addDueDateDeltaAlertWithDueDateDelta:(id)delta{return delta;}
@end
@interface FixtureReminderChange:NSObject
@property FixtureFlagChange *flaggedContext;
@property FixtureEarlyChange *dueDateDeltaAlertContext;
@property FixtureUrgentChange *urgentAlarmContext;
@property FixtureTagChange *hashtagContext;
@property id subtaskContext;
@end
@implementation FixtureReminderChange
@end
@interface FixtureListChange:NSObject
-(id)sectionsContextChangeItem;
@end
@implementation FixtureListChange
-(id)sectionsContextChangeItem{return [NSObject new];}
@end
@interface FixtureSave:NSObject
@property FixtureReminderChange *change;
-(id)initWithStore:(id)store;
-(id)updateReminder:(id)reminder;
-(id)updateList:(id)list;
-(id)addListSectionWithDisplayName:(id)title toListSectionContextChangeItem:(id)context listSectionObjectID:(id)identity;
-(id)addReminderWithTitle:(id)title toReminderSubtaskContextChangeItem:(id)context reminderObjectID:(id)identity;
-(BOOL)saveSynchronouslyWithError:(NSError **)error;
@end
@implementation FixtureSave
-(id)initWithStore:(id)store{return [super init];}
-(id)updateList:(id)list{return [FixtureListChange new];}
-(id)addListSectionWithDisplayName:(id)title toListSectionContextChangeItem:(id)context listSectionObjectID:(id)identity{return [NSObject new];}
-(id)updateReminder:(id)reminder{self.change=[FixtureReminderChange new];self.change.dueDateDeltaAlertContext=[FixtureEarlyChange new];self.change.subtaskContext=[NSObject new];self.change.flaggedContext=[FixtureFlagChange new];self.change.urgentAlarmContext=[FixtureUrgentChange new];self.change.urgentAlarmContext.enabled=((FixtureUrgent *)fixture.urgentAlarmContext).enabled;self.change.hashtagContext=[FixtureTagChange new];self.change.hashtagContext.hashtags=[NSMutableArray arrayWithArray:((FixtureTags *)fixture.hashtagContext).hashtags ?: @[]];return self.change;}
-(id)addReminderWithTitle:(id)title toReminderSubtaskContextChangeItem:(id)context reminderObjectID:(id)identity{return [NSObject new];}
-(BOOL)saveSynchronouslyWithError:(NSError **)error{flagSaves++;if(saveFails){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:nil];return NO;}((FixtureFlag *)fixture.flaggedContext).flagged=self.change.flaggedContext.flagged;if(fixture.hashtagContext)((FixtureTags *)fixture.hashtagContext).hashtags=self.change.hashtagContext.hashtags;if(fixture.urgentAlarmContext)((FixtureUrgent *)fixture.urgentAlarmContext).enabled=self.change.urgentAlarmContext.enabled;return YES;}
@end
static FixtureID *Identity(NSString *entity,NSString *value){FixtureID *v=[FixtureID new];v.entityName=entity;v.uuid=[[NSUUID alloc]initWithUUIDString:value];return v;}
static NSDictionary *CreateSubtask(NSDictionary *request,BOOL allow){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersCreateSubtask(data,^{return allow;}) options:0 error:NULL];}
static NSDictionary *SetUrgent(NSDictionary *request,BOOL allow){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersSetUrgent(data,^{return allow;}) options:0 error:NULL];}
static NSDictionary *Urgent(NSDictionary *request){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadUrgent(data) options:0 error:NULL];}
static NSDictionary *Children(NSDictionary *request){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadChildren(data) options:0 error:NULL];}
static NSDictionary *Hierarchy(NSDictionary *request){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadHierarchy(data) options:0 error:NULL];}
static NSDictionary *Tags(NSDictionary *request){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadTags(data) options:0 error:NULL];}
static NSDictionary *Call(NSDictionary *request){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadFlag(data) options:0 error:NULL];}
static NSDictionary *SetFlag(NSDictionary *request,BOOL allow){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersSetFlag(data,^{return allow;}) options:0 error:NULL];}
static NSDictionary *ReplaceTag(NSDictionary *request,BOOL allow){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReplaceTag(data,^{return allow;}) options:0 error:NULL];}
static NSDictionary *AddTag(NSDictionary *request,BOOL allow){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersAddTag(data,^{return allow;}) options:0 error:NULL];}
static NSDictionary *RemoveTag(NSDictionary *request,BOOL allow){NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersRemoveTag(data,^{return allow;}) options:0 error:NULL];}
int main(){@autoreleasepool{
 NSString *task=@"11111111-1111-4111-8111-111111111111",*list=@"22222222-2222-4222-8222-222222222222",*source=@"33333333-3333-4333-8333-333333333333",*other=@"44444444-4444-4444-8444-444444444444";
 fixture=[FixtureReminder new];fixture.remObjectID=Identity(@"REMCDReminder",task);fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);fixture.list=[FixtureList new];fixture.list.remObjectID=Identity(@"REMCDList",list);fixture.list.account=fixture.account;FixtureFlag *flag=[FixtureFlag new];fixture.flaggedContext=flag;
 FixtureSection *section=[FixtureSection new];section.remObjectID=Identity(@"REMCDListSection",other);section.list=fixture.list;section.storage=[FixtureSectionStorage new];section.storage.accountID=fixture.account.remObjectID;section.storage.listID=fixture.list.remObjectID;section.storage.displayName=@"Synthetic";fixtureSections=@[section];
 NSDictionary *sectionRequest=@{@"source_id":source,@"list_id":list};NSData *sectionData=[NSJSONSerialization dataWithJSONObject:sectionRequest options:0 error:NULL];NSDictionary *(^readSections)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersListSections(sectionData) options:0 error:NULL];};
 NSCAssert([readSections()[@"sections"] count]==1 && [readSections()[@"sections"][0][@"title"] isEqual:@"Synthetic"],@"exact section read");
 NSData *membershipData=[NSJSONSerialization dataWithJSONObject:@{@"id":task,@"source_id":source,@"list_id":list} options:0 error:NULL];NSDictionary *(^membership)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadSection(membershipData) options:0 error:NULL];};
 NSCAssert([membership()[@"native_metadata"][@"section_status"] isEqual:@"provider_reported"],@"section membership observed");
 section.storage.accountID=Identity(@"REMCDAccount",other);NSCAssert(readSections()[@"error"] && membership()[@"error"],@"foreign section account refused");section.storage.accountID=fixture.account.remObjectID;
 section.list.account.remObjectID=Identity(@"REMCDAccount",other);NSCAssert(membership()[@"error"],@"foreign account membership refused");section.list.account.remObjectID=Identity(@"REMCDAccount",source);section.storage.accountID=fixture.account.remObjectID;
 fixtureSections=@[section,section];NSCAssert(readSections()[@"error"],@"duplicate sections refused");fixtureSections=@[];NSCAssert([readSections()[@"sections"] count]==0,@"empty list distinct from error");
 NSCAssert([membership()[@"native_metadata"][@"section_status"] isEqual:@"provider_not_reported"] && membership()[@"native_metadata"][@"section"]==[NSNull null],@"unreported membership is not inferred absent");
 fixture.storage=[FixtureStorage new];NSDictionary *(^early)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadEarly(membershipData) options:0 error:NULL];};
 NSCAssert([early()[@"native_metadata"][@"early_alerts"] count]==0 && early()[@"native_metadata"][@"early_payload_fingerprint"]==[NSNull null],@"empty early state observed");
 fixture.storage.hasUnfetchedDueDateDeltaAlerts=YES;NSCAssert(early()[@"error"],@"unfetched payload cannot mean empty");
 NSMutableDictionary *payload=[@{@"minimumSupportedVersion":@20230430,@"accountIdentifier":source,@"reminderIdentifier":task,@"dueDateDeltaAlerts":@[@{@"dueDateDeltaUnit":@0,@"dueDateDeltaCount":@-5}]} mutableCopy];fixture.storage.dueDateDeltaAlertsData=[NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];
 NSCAssert([early()[@"native_metadata"][@"early_alerts"][0][@"native_count"] integerValue]==-5 && [early()[@"native_metadata"][@"early_payload_fingerprint"] length]==64,@"scoped early payload observed");
 payload[@"minimumSupportedVersion"]=@20240430;fixture.storage.dueDateDeltaAlertsData=[NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];NSCAssert(early()[@"error"],@"unknown provider version refused");payload[@"minimumSupportedVersion"]=@20230430;
 payload[@"accountIdentifier"]=other;fixture.storage.dueDateDeltaAlertsData=[NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];NSCAssert(early()[@"error"],@"foreign early payload refused");
 payload[@"accountIdentifier"]=source;payload[@"dueDateDeltaAlerts"]=@[@{@"dueDateDeltaUnit":@YES,@"dueDateDeltaCount":@-5}];fixture.storage.dueDateDeltaAlertsData=[NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];NSCAssert(early()[@"error"],@"boolean interval refused");
 fixture.storage.dueDateDeltaAlertsData=[NSMutableData dataWithLength:65537];NSCAssert(early()[@"error"],@"oversized payload refused");fixture.storage.hasUnfetchedDueDateDeltaAlerts=NO;fixture.storage.dueDateDeltaAlertsData=nil;
 NSDictionary *earlyAdd=@{@"id":task,@"source_id":source,@"list_id":list,@"unit":@0,@"count":@-5};NSData *earlyAddData=[NSJSONSerialization dataWithJSONObject:earlyAdd options:0 error:NULL];int earlySaves=flagSaves;
 NSDictionary *(^addEarly)(BOOL)=^NSDictionary *(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersAddEarly(earlyAddData,^{return allow;}) options:0 error:NULL];};
 NSCAssert(addEarly(NO)[@"error"] && flagSaves==earlySaves,@"closed early gate never saves");
 NSCAssert(addEarly(YES)[@"result"] && flagSaves==earlySaves+1,@"one fixed early write saves only after gate");
 fixture.storage.hasUnfetchedDueDateDeltaAlerts=YES;NSCAssert(addEarly(YES)[@"error"] && flagSaves==earlySaves+1,@"unavailable early state refuses save");fixture.storage.hasUnfetchedDueDateDeltaAlerts=NO;flagSaves=0;
 NSDictionary *createSection=@{@"source_id":source,@"list_id":list,@"section_id":other,@"title":@"Synthetic new",@"expected_sections":@[]};NSData *createData=[NSJSONSerialization dataWithJSONObject:createSection options:0 error:NULL];NSDictionary *closed=[NSJSONSerialization JSONObjectWithData:ICloudRemindersCreateSection(createData,^{return NO;}) options:0 error:NULL];NSCAssert(closed[@"error"] && flagSaves==0,@"closed section dispatch gate never saves");
 NSDictionary *request=@{@"id":task,@"source_id":source,@"list_id":list};
 NSDictionary *set=@{@"id":task,@"source_id":source,@"list_id":list,@"expected_flagged":@NO,@"flagged":@YES};
 NSCAssert(SetFlag(set,NO)[@"error"] && flagSaves==0 && flag.flagged==0,@"closed gate never saves");
 NSCAssert(SetFlag(@{@"id":other,@"source_id":source,@"list_id":list,@"expected_flagged":@NO,@"flagged":@YES},YES)[@"error"] && flagSaves==0,@"wrong note never saves");
 NSCAssert(SetFlag(@{@"id":task,@"source_id":source,@"list_id":list,@"expected_flagged":@NO,@"flagged":@1},YES)[@"error"] && flagSaves==0,@"integer is not Boolean");
 NSCAssert(SetFlag(set,YES)[@"result"] && flagSaves==1 && flag.flagged==1,@"one scoped save changes flag");
 NSCAssert(SetFlag(set,YES)[@"error"] && flagSaves==1,@"stale snapshot never saves");
 NSDictionary *clear=@{@"id":task,@"source_id":source,@"list_id":list,@"expected_flagged":@YES,@"flagged":@NO};
 saveFails=YES;NSDictionary *failed=SetFlag(clear,YES);NSCAssert([failed[@"mutation_dispatched"] boolValue] && flag.flagged==1,@"save failure remains uncertain");saveFails=NO;
 NSCAssert(SetFlag(clear,YES)[@"result"] && flag.flagged==0,@"flag cleared");
 NSDictionary *first=Call(request);NSCAssert(first[@"native_metadata"] && ![first[@"native_metadata"][@"flagged"] boolValue],@"unflagged actual integer metadata");
 NSCAssert(CFGetTypeID((__bridge CFTypeRef)first[@"native_metadata"][@"flagged"])==CFBooleanGetTypeID(),@"JSON boolean not integer");
 flag.flagged=1;NSDictionary *second=Call(request);NSCAssert([second[@"native_metadata"][@"flagged"] boolValue] && ![second[@"native_metadata"][@"native_metadata_complete"] boolValue],@"flagged and bounded coverage");
 fixture.remObjectID.entityName=@"REMReminder";NSCAssert([Call(request)[@"category"] isEqual:@"native_identity_mismatch"],@"native entity names bound as well as UUIDs");fixture.remObjectID.entityName=@"REMCDReminder";
 flag.flagged=2;NSCAssert([Call(request)[@"category"] isEqual:@"unknown_flag_state"],@"unknown never false");flag.flagged=0;
 fixture.flaggedContext=nil;NSCAssert([Call(request)[@"category"] isEqual:@"unsupported_flag_metadata"],@"nil never false");fixture.flaggedContext=flag;
 for(NSString *key in @[@"id",@"source_id",@"list_id"]){NSMutableDictionary *changed=[request mutableCopy];changed[key]=other;NSCAssert(Call(changed)[@"error"],@"scope refusal");}
 FixtureAccount *foreign=[FixtureAccount new];foreign.remObjectID=Identity(@"REMCDAccount",other);fixture.list.account=foreign;NSCAssert([Call(request)[@"category"] isEqual:@"native_identity_mismatch"],@"account relation bound");fixture.list.account=fixture.account;
 denied=YES;NSDictionary *failure=Call(request);NSCAssert([failure[@"category"] isEqual:@"private_read_denied_or_unavailable"] && [failure[@"native_error_domain"] isEqual:@"other"] && ![[failure description] containsString:@"private-content"],@"denial not empty and private diagnostics redacted");denied=NO;
 NSCAssert(Call(@{@"id":@YES,@"source_id":source,@"list_id":list})[@"error"],@"bad types");
 NSCAssert(Call(@{@"id":task,@"source_id":source,@"list_id":list,@"selector":@"anything"})[@"error"],@"no arbitrary selectors");
 FixtureUrgent *urgent=[FixtureUrgent new];fixture.urgentAlarmContext=urgent;
 NSDictionary *urgentRead=Urgent(request);NSCAssert(urgentRead[@"native_metadata"] && CFGetTypeID((__bridge CFTypeRef)urgentRead[@"native_metadata"][@"urgent"])==CFBooleanGetTypeID() && ![urgentRead[@"native_metadata"][@"urgent"] boolValue],@"false urgent is a JSON boolean");
 NSDictionary *urgentChange=@{@"id":task,@"source_id":source,@"list_id":list,@"urgent":@YES,@"expected_urgent":@NO};int urgentSaves=flagSaves;
 NSCAssert(SetUrgent(urgentChange,NO)[@"error"] && flagSaves==urgentSaves && !urgent.enabled,@"closed gate prevents urgent write");
 NSCAssert(SetUrgent(urgentChange,YES)[@"result"] && urgent.enabled,@"urgent setting saved");
 NSCAssert(SetUrgent(urgentChange,YES)[@"error"],@"stale urgent value refused");
 urgent.enabled=YES;NSCAssert([Urgent(request)[@"native_metadata"][@"urgent"] boolValue],@"true urgent observed");fixture.urgentAlarmContext=nil;NSCAssert(Urgent(request)[@"error"],@"unavailable urgent not false");
 FixtureTags *context=[FixtureTags new];context.hashtags=@[];fixture.hashtagContext=context;
 NSCAssert([Tags(request)[@"native_metadata"][@"tags_complete"] boolValue] && [Tags(request)[@"native_metadata"][@"tags"] count]==0,@"verified empty collection");
 FixtureTag *tag=[FixtureTag new];tag.remObjectID=Identity(@"REMCDHashtag",other);tag.accountID=fixture.account.remObjectID;tag.reminderID=fixture.remObjectID;tag.name=@"Project";tag.type=0;context.hashtags=@[tag];
 NSDictionary *tagResult=Tags(request);NSCAssert([tagResult[@"native_metadata"][@"tags"][0][@"name"] isEqual:@"Project"],@"native tag exposed");
 FixtureTag *secondTag=[FixtureTag new];secondTag.remObjectID=Identity(@"REMCDHashtag",@"55555555-5555-4555-8555-555555555555");secondTag.accountID=fixture.account.remObjectID;secondTag.reminderID=fixture.remObjectID;secondTag.name=@"Other";secondTag.type=0;context.hashtags=@[tag,secondTag];
 NSArray *expected=Tags(request)[@"native_metadata"][@"tags"];NSDictionary *remove=@{@"id":task,@"source_id":source,@"list_id":list,@"tag_id":other,@"expected_tags":expected};int savesBefore=flagSaves;
 NSDictionary *replace=@{@"id":task,@"source_id":source,@"list_id":list,@"tag_id":other,@"tag_name":@"SyntheticNew",@"expected_tags":expected};
 NSCAssert(ReplaceTag(replace,NO)[@"error"] && flagSaves==savesBefore && [context.hashtags count]==2,@"closed gate leaves old tags");
 saveFails=YES;NSDictionary *uncertain=ReplaceTag(replace,YES);NSCAssert([uncertain[@"mutation_dispatched"] boolValue] && [uncertain[@"error"] isEqual:@"mutation_failed_or_uncertain"] && [context.hashtags count]==2,@"post-dispatch failure remains uncertain");saveFails=NO;
 NSCAssert(ReplaceTag(replace,YES)[@"result"] && [context.hashtags count]==2 && [context.hashtags containsObject:secondTag],@"replace one and preserve other tag");
 NSCAssert(ReplaceTag(replace,YES)[@"error"] && [context.hashtags count]==2,@"stale snapshot refused");context.hashtags=@[tag,secondTag];savesBefore=flagSaves;
 NSDictionary *add=@{@"id":task,@"source_id":source,@"list_id":list,@"tag_name":@"Café東京",@"expected_tags":expected};
 NSCAssert(AddTag(add,NO)[@"error"] && flagSaves==savesBefore && [context.hashtags count]==2,@"closed gate leaves native tags untouched");
 NSCAssert(AddTag(add,YES)[@"result"] && [context.hashtags count]==3,@"exact native tag added");
 NSCAssert(AddTag(add,YES)[@"error"] && [context.hashtags count]==3,@"stale snapshot refused");context.hashtags=@[tag,secondTag];savesBefore=flagSaves;
 NSCAssert(RemoveTag(remove,NO)[@"error"] && flagSaves==savesBefore && [context.hashtags count]==2,@"closed gate leaves both tags");
 NSCAssert(RemoveTag(remove,YES)[@"result"] && [context.hashtags count]==1 && context.hashtags[0]==secondTag,@"remove only selected tag");
 NSCAssert(RemoveTag(remove,YES)[@"error"] && [context.hashtags count]==1,@"stale tags refused");context.hashtags=@[tag];
 context.hashtags=[NSSet setWithObject:tag];NSCAssert(Tags(request)[@"native_metadata"],@"native set collection");
 context.hashtags=[NSOrderedSet orderedSetWithObject:tag];NSCAssert(Tags(request)[@"native_metadata"],@"native ordered collection");
 context.hashtags=@[tag,tag];NSCAssert(Tags(request)[@"error"],@"duplicate identities refused");context.hashtags=@[tag];
 tag.accountID=Identity(@"REMCDAccount",other);NSCAssert(Tags(request)[@"error"],@"foreign account refused");tag.accountID=fixture.account.remObjectID;
 tag.reminderID=Identity(@"REMCDReminder",other);NSCAssert(Tags(request)[@"error"],@"foreign task refused");tag.reminderID=fixture.remObjectID;
 tag.name=[@"x" stringByPaddingToLength:513 withString:@"x" startingAtIndex:0];NSCAssert(Tags(request)[@"error"],@"oversized tag refused");tag.name=@"Project";
 NSMutableArray *tooMany=[NSMutableArray array];for(int i=0;i<201;i++)[tooMany addObject:tag];context.hashtags=tooMany;NSCAssert(Tags(request)[@"error"],@"bounded collection refused");
 context.hashtags=nil;NSCAssert(Tags(request)[@"error"],@"nil tags not empty success");
 context.hashtags=@"unknown";NSCAssert(Tags(request)[@"error"],@"unknown collection refused");
 fixture.hashtagContext=nil;NSCAssert(Tags(request)[@"error"],@"missing context refused");
 FixtureHierarchy *hierarchy=[FixtureHierarchy new];fixture.subtaskContext=hierarchy;
 NSDictionary *hint=Hierarchy(request)[@"native_metadata"];NSCAssert(hint && ![hint[@"has_subtasks"] boolValue] && ![hint[@"hierarchy_complete"] boolValue] && hint[@"parent_id"]==[NSNull null],@"unparented and provider child hint");
 hierarchy.hasChildren=YES;NSCAssert([Hierarchy(request)[@"native_metadata"][@"has_subtasks"] boolValue],@"reported child presence");
 fixture.isSubtask=YES;NSCAssert(Hierarchy(request)[@"error"],@"missing parent never success");
 FixtureReminder *parent=[FixtureReminder new];parent.remObjectID=Identity(@"REMCDReminder",other);parent.account=fixture.account;parent.list=fixture.list;fixture.parentReminder=parent;
 NSCAssert([Hierarchy(request)[@"native_metadata"][@"parent_id"] isEqual:other],@"scoped parent identity");
 parent.account=[FixtureAccount new];parent.account.remObjectID=Identity(@"REMCDAccount",other);NSCAssert(Hierarchy(request)[@"error"],@"foreign parent refused");parent.account=fixture.account;
 parent.remObjectID=fixture.remObjectID;NSCAssert(Hierarchy(request)[@"error"],@"self parent refused");parent.remObjectID=Identity(@"REMCDReminder",other);
 hierarchy.denied=YES;NSCAssert([Hierarchy(request)[@"category"] isEqual:@"hierarchy_read_unavailable"],@"read error not false");hierarchy.denied=NO;
 fixture.isSubtask=NO;NSCAssert(Hierarchy(request)[@"error"],@"contradictory parent refused");fixture.parentReminder=nil;fixture.subtaskContext=nil;NSDictionary *partial=Hierarchy(request)[@"native_metadata"];NSCAssert(partial && partial[@"has_subtasks"]==[NSNull null] && [partial[@"children_status"] isEqual:@"unavailable"] && ![partial[@"hierarchy_complete"] boolValue],@"missing context is unknown, never no children");
 fixture.isSubtask=YES;fixture.parentReminder=parent;partial=Hierarchy(request)[@"native_metadata"];NSCAssert([partial[@"parent_id"] isEqual:other] && partial[@"has_subtasks"]==[NSNull null],@"known scoped parent with unavailable child metadata");
 fixture.isSubtask=NO;fixture.parentReminder=nil;fixture.subtaskContext=hierarchy;hierarchy.children=@[];
 NSDictionary *children=Children(request)[@"native_metadata"];NSCAssert(children && [children[@"child_ids"] count]==0 && ![children[@"children_complete"] boolValue],@"bounded provider view");
 FixtureReminder *child=[FixtureReminder new];child.remObjectID=Identity(@"REMCDReminder",other);child.account=fixture.account;child.list=fixture.list;child.parentReminder=fixture;child.isSubtask=YES;hierarchy.children=@[child];
 NSCAssert([Children(request)[@"native_metadata"][@"child_ids"][0] isEqual:other],@"exact direct child");
 child.parentReminder=nil;NSCAssert(Children(request)[@"error"],@"foreign or missing parent refused");child.parentReminder=fixture;
 child.isSubtask=NO;NSCAssert(Children(request)[@"error"],@"non-subtask refused");child.isSubtask=YES;
 child.account=[FixtureAccount new];child.account.remObjectID=Identity(@"REMCDAccount",other);NSCAssert(Children(request)[@"error"],@"foreign account refused");child.account=fixture.account;
 NSString *newChild=@"66666666-6666-4666-8666-666666666666";NSDictionary *create=@{@"id":task,@"source_id":source,@"list_id":list,@"child_id":newChild,@"title":@"Synthetic child",@"expected_children":@[other]};int subtaskSaves=flagSaves;
 childCollision=YES;NSCAssert(CreateSubtask(create,YES)[@"error"] && flagSaves==subtaskSaves,@"existing ID refused");childCollision=NO;
 childLookupError=YES;NSCAssert(CreateSubtask(create,YES)[@"error"] && flagSaves==subtaskSaves,@"unexpected lookup failure refused");childLookupError=NO;
 NSCAssert(CreateSubtask(create,NO)[@"error"] && flagSaves==subtaskSaves,@"closed gate prevents child save");
 NSDictionary *created=CreateSubtask(create,YES);NSCAssert([created[@"result"][@"id"] isEqual:newChild] && flagSaves==subtaskSaves+1,@"fixed preassigned identity returned");
 NSMutableDictionary *stale=[create mutableCopy];stale[@"expected_children"]=@[];NSCAssert(CreateSubtask(stale,YES)[@"error"] && flagSaves==subtaskSaves+1,@"changed children refuse before save");
 saveFails=YES;NSDictionary *failedCreate=CreateSubtask(create,YES);NSCAssert([failedCreate[@"mutation_dispatched"] boolValue] && [failedCreate[@"error"] isEqual:@"mutation_failed_or_uncertain"],@"post-dispatch child save error remains uncertain");saveFails=NO;
 hierarchy.children=@[child,child];NSCAssert(Children(request)[@"error"],@"duplicate child refused");
 NSMutableArray *over=[NSMutableArray array];for(int i=0;i<101;i++)[over addObject:child];hierarchy.children=over;NSCAssert(Children(request)[@"error"],@"children bound");
 hierarchy.children=nil;NSCAssert(Children(request)[@"error"],@"nil provider result refused");
 hierarchy.denied=YES;NSCAssert([Children(request)[@"category"] isEqual:@"children_read_unavailable"],@"provider error refused");hierarchy.denied=NO;
 fixture.subtaskContext=nil;children=Children(request)[@"native_metadata"];NSCAssert(children[@"child_ids"]==[NSNull null] && [children[@"children_fetch_status"] isEqual:@"unavailable"],@"missing context not empty success");
 puts("fixed native flag read: scope, types, denied/missing/unknown refusal passed");return 0;
}}
'''
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();source=source.replace('    if(NSProcessInfo.processInfo.operatingSystemVersion.majorVersion!=27)return NO;','').replace('    if(!dlopen("/System/Library/PrivateFrameworks/ReminderKit.framework/ReminderKit",RTLD_LAZY|RTLD_LOCAL))return NO;','')
    source=source.replace('NSProcessInfo.processInfo.operatingSystemVersion.majorVersion!=27 || !dlopen("/System/Library/PrivateFrameworks/ReminderKit.framework/ReminderKit",RTLD_LAZY|RTLD_LOCAL)','NO')
    for real,fake in [('REMDueDateDeltaInterval','FixtureDelta'),('REMStore','FixtureStore'),('REMObjectID','FixtureID'),('REMReminder','FixtureReminder'),('REMSaveRequest','FixtureSave'),('REMListSectionsDataView','FixtureSectionView')]:source=source.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
    with tempfile.TemporaryDirectory(prefix='native-flag-contract-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(source+'\n'+FIXTURE)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['actual_fixed_read_and_runtime_signatures','true_and_false_integer_flag','exact_task_list_source_and_account_relation','nil_unknown_or_denied_not_empty_success','bounded_tags_and_exact_account_task_identity','hierarchy_parent_scope_and_unknown_refusal','bounded_direct_children_and_scope_refusals','fixed_unsaved_gate_and_synthetic_save_outcomes','no_arbitrary_selectors']}))
if __name__=='__main__':run()
