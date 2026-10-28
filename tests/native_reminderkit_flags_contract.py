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
@interface FixtureListStorage:NSObject
@property BOOL shouldCategorizeGroceryItems;
@property BOOL shouldSupportGroceryShoppingSession;
@property BOOL isGroup;
@property id parentListID;
@property id accountID;
@property NSString *name;
@end
@implementation FixtureListStorage
@end
@interface FixtureList:NSObject
@property FixtureID *remObjectID;
@property FixtureAccount *account;
@property FixtureListStorage *storage;
@property BOOL isShared;
@property BOOL isSharedToMe;
@property BOOL canBeShared;
@property BOOL isPinned;
@property id parentList;
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
@interface FixtureAttachment:NSObject
@property FixtureID *remObjectID;
@property FixtureID *accountID;
@property FixtureID *reminderID;
@property NSString *uti;
@end
@implementation FixtureAttachment
@end
@interface FixtureAttachmentContext:NSObject
@property id attachments;
@end
@implementation FixtureAttachmentContext
@end
@interface FixtureAssignment:NSObject
@property FixtureID *accountID;
@property FixtureID *reminderID;
@end
@implementation FixtureAssignment
@end
@interface FixtureAssignments:NSObject
@property id currentAssignment;
@property id assignments;
@end
@implementation FixtureAssignments
@end
@interface FixtureReminder:NSObject
@property FixtureID *remObjectID;
@property FixtureAccount *account;
@property FixtureList *list;
@property FixtureStorage *storage;
@property id flaggedContext;
@property id assignmentContext;
@property id attachmentContext;
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
static NSArray *fixtureGroupChildren;
@interface FixtureListsView:NSObject
-(id)initWithStore:(id)store;
-(id)fetchListsInGroup:(id)group error:(NSError **)error;
@end
@implementation FixtureListsView
-(id)initWithStore:(id)store{return [super init];}
-(id)fetchListsInGroup:(id)group error:(NSError **)error{return fixtureGroupChildren;}
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
-(id)fetchListSectionWithObjectID:(id)objectID error:(NSError **)error{for(FixtureSection *section in fixtureSections)if([section.remObjectID.uuid isEqual:[objectID uuid]])return section;*error=[NSError errorWithDomain:@"com.apple.reminderkit" code:-3000 userInfo:nil];return nil;}
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
@property NSArray *removedIdentifiers;
-(void)removeDueDateDeltaAlertsWithIdentifiers:(NSArray *)identifiers;
-(id)addDueDateDeltaAlertWithDueDateDelta:(id)delta;
@end
@implementation FixtureEarlyChange
-(void)removeDueDateDeltaAlertsWithIdentifiers:(NSArray *)identifiers{NSCAssert(identifiers.count==1 && [identifiers[0] isKindOfClass:NSUUID.class],@"exact native UUID object required");self.removedIdentifiers=identifiers;}
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
@interface FixtureSectionChange:NSObject
@property NSString *displayName;
@property FixtureSectionStorage *storage;
@end
@implementation FixtureSectionChange
-(void)setDisplayName:(NSString *)name {self.storage.displayName=name;}
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
-(id)updateListSection:(id)section;
-(id)addListSectionWithDisplayName:(id)title toListSectionContextChangeItem:(id)context listSectionObjectID:(id)identity;
-(id)addReminderWithTitle:(id)title toReminderSubtaskContextChangeItem:(id)context reminderObjectID:(id)identity;
-(BOOL)saveSynchronouslyWithError:(NSError **)error;
@end
@implementation FixtureSave
-(id)initWithStore:(id)store{return [super init];}
-(id)updateList:(id)list{return [FixtureListChange new];}
-(id)updateListSection:(id)section{FixtureSectionChange *c=[FixtureSectionChange new];c.storage=[FixtureSectionStorage new];return c;}
-(id)addListSectionWithDisplayName:(id)title toListSectionContextChangeItem:(id)context listSectionObjectID:(id)identity{return [NSObject new];}
-(id)updateReminder:(id)reminder{self.change=[FixtureReminderChange new];self.change.dueDateDeltaAlertContext=[FixtureEarlyChange new];self.change.subtaskContext=[NSObject new];self.change.flaggedContext=[FixtureFlagChange new];self.change.urgentAlarmContext=[FixtureUrgentChange new];self.change.urgentAlarmContext.enabled=((FixtureUrgent *)fixture.urgentAlarmContext).enabled;self.change.hashtagContext=[FixtureTagChange new];self.change.hashtagContext.hashtags=[NSMutableArray arrayWithArray:((FixtureTags *)fixture.hashtagContext).hashtags ?: @[]];return self.change;}
-(id)addReminderWithTitle:(id)title toReminderSubtaskContextChangeItem:(id)context reminderObjectID:(id)identity{return [NSObject new];}
-(BOOL)saveSynchronouslyWithError:(NSError **)error{flagSaves++;if(saveFails){*error=[NSError errorWithDomain:@"synthetic" code:1 userInfo:nil];return NO;}if(self.change.dueDateDeltaAlertContext.removedIdentifiers.count==1)fixture.storage.dueDateDeltaAlertsData=nil;((FixtureFlag *)fixture.flaggedContext).flagged=self.change.flaggedContext.flagged;if(fixture.hashtagContext)((FixtureTags *)fixture.hashtagContext).hashtags=self.change.hashtagContext.hashtags;if(fixture.urgentAlarmContext)((FixtureUrgent *)fixture.urgentAlarmContext).enabled=self.change.urgentAlarmContext.enabled;return YES;}
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
 fixture.list.storage=[FixtureListStorage new];fixture.list.storage.accountID=fixture.account.remObjectID;NSData *listData=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"list_id":list} options:0 error:NULL];NSDictionary *(^listFeatures)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(listData) options:0 error:NULL];};
 NSData *pinData=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"list_id":list,@"include_pinned":@YES} options:0 error:NULL];NSDictionary *(^pinFeatures)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(pinData) options:0 error:NULL];};
 NSCAssert([pinFeatures()[@"backend"] isEqual:@"reminderkit-list-features-v3"] && ![pinFeatures()[@"pinned"] boolValue],@"unpinned state observed");fixture.list.isPinned=YES;NSCAssert([pinFeatures()[@"pinned"] boolValue] && listFeatures()[@"pinned"]==nil,@"pinned state opt-in");fixture.list.isPinned=NO;
 NSData *badPinData=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"list_id":list,@"include_pinned":@1} options:0 error:NULL];NSDictionary *badPin=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(badPinData) options:0 error:NULL];NSCAssert(badPin[@"error"],@"pin selection requires a true Boolean");
 NSCAssert(![listFeatures()[@"grocery_categorization"] boolValue] && ![listFeatures()[@"list_type_verified"] boolValue],@"ordinary provider flags do not claim type");fixture.list.storage.shouldCategorizeGroceryItems=YES;NSCAssert([listFeatures()[@"grocery_categorization"] boolValue],@"native grocery flag read");fixture.list.isShared=YES;fixture.list.isSharedToMe=YES;NSCAssert([listFeatures()[@"shared"] boolValue] && [listFeatures()[@"shared_to_me"] boolValue] && ![listFeatures()[@"sharing_metadata_complete"] boolValue],@"sharing flags do not claim participant details");fixture.list.account.remObjectID=Identity(@"REMCDAccount",other);NSCAssert(listFeatures()[@"error"],@"foreign list account refused");fixture.list.account.remObjectID=Identity(@"REMCDAccount",source);
 FixtureSection *section=[FixtureSection new];section.remObjectID=Identity(@"REMCDListSection",other);section.list=fixture.list;section.storage=[FixtureSectionStorage new];section.storage.accountID=fixture.account.remObjectID;section.storage.listID=fixture.list.remObjectID;section.storage.displayName=@"Synthetic";fixtureSections=@[section];
 fixture.list.storage.isGroup=YES;FixtureList *childList=[FixtureList new];childList.remObjectID=Identity(@"REMCDList",other);childList.account=fixture.account;childList.storage=[FixtureListStorage new];childList.storage.accountID=fixture.account.remObjectID;childList.storage.name=@"Synthetic child";childList.storage.parentListID=fixture.list.remObjectID;fixtureGroupChildren=@[childList];
 NSData *groupData=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"group_id":list,@"limit":@100,@"offset":@0} options:0 error:NULL];NSDictionary *(^groupRead)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupLists(groupData) options:0 error:NULL];};
 NSCAssert([groupRead()[@"lists"][0][@"title"] isEqual:@"Synthetic child"] && ![groupRead()[@"lists"][0][@"is_group"] boolValue] && [groupRead()[@"membership_complete"] boolValue]==NO,@"scoped group child observed");childList.storage.isGroup=YES;NSCAssert([groupRead()[@"lists"][0][@"is_group"] boolValue],@"nested group flag observed");childList.storage.isGroup=NO;
 FixtureList *secondChild=[FixtureList new];secondChild.remObjectID=Identity(@"REMCDList",@"77777777-7777-4777-8777-777777777777");secondChild.account=fixture.account;secondChild.storage=[FixtureListStorage new];secondChild.storage.accountID=fixture.account.remObjectID;secondChild.storage.name=@"Second child";secondChild.storage.parentListID=fixture.list.remObjectID;fixtureGroupChildren=@[secondChild,childList];
 NSData *pageData=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"group_id":list,@"limit":@1,@"offset":@0} options:0 error:NULL];NSDictionary *firstPage=[NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupLists(pageData) options:0 error:NULL];NSCAssert([firstPage[@"lists"] count]==1 && ![firstPage[@"done"] boolValue] && [firstPage[@"next_offset"] integerValue]==1,@"group page progresses");
 pageData=[NSJSONSerialization dataWithJSONObject:@{@"source_id":source,@"group_id":list,@"limit":@1,@"offset":@1} options:0 error:NULL];NSDictionary *secondPage=[NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupLists(pageData) options:0 error:NULL];NSCAssert([secondPage[@"lists"] count]==1 && [secondPage[@"done"] boolValue] && [secondPage[@"next_offset"] integerValue]==2,@"group page completes");fixtureGroupChildren=@[childList];
 childList.storage.parentListID=Identity(@"REMCDList",other);NSCAssert(groupRead()[@"error"],@"wrong group parent refused");childList.storage.parentListID=fixture.list.remObjectID;childList.storage.accountID=Identity(@"REMCDAccount",other);NSCAssert(groupRead()[@"error"],@"foreign child storage refused");childList.storage.accountID=fixture.account.remObjectID;fixtureGroupChildren=@[childList,childList];NSCAssert(groupRead()[@"error"],@"duplicate group child refused");fixture.list.storage.isGroup=NO;NSCAssert(groupRead()[@"error"],@"ordinary list refused");
 NSDictionary *parentReq=@{@"source_id":source,@"list_id":list};NSData *parentData=[NSJSONSerialization dataWithJSONObject:parentReq options:0 error:NULL];NSDictionary *(^parentRead)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListParent(parentData) options:0 error:NULL];};
 NSCAssert([parentRead()[@"parent_status"] isEqual:@"provider_not_reported"],@"unreported parent observed");
 FixtureList *group=[FixtureList new];group.remObjectID=Identity(@"REMCDList",other);group.account=fixture.account;group.storage=[FixtureListStorage new];group.storage.accountID=fixture.account.remObjectID;group.storage.isGroup=YES;group.storage.name=@"Synthetic group";fixture.list.parentList=group;fixture.list.storage.parentListID=group.remObjectID;
 NSCAssert([parentRead()[@"parent"][@"title"] isEqual:@"Synthetic group"],@"scoped parent observed");
 group.account=[FixtureAccount new];group.account.remObjectID=Identity(@"REMCDAccount",other);NSCAssert(parentRead()[@"error"],@"foreign parent refused");group.account=fixture.account;group.storage.accountID=Identity(@"REMCDAccount",other);NSCAssert(parentRead()[@"error"],@"foreign parent storage refused");group.storage.accountID=fixture.account.remObjectID;fixture.list.parentList=nil;NSCAssert(parentRead()[@"error"],@"inconsistent parent refused");fixture.list.storage.parentListID=nil;
 NSDictionary *sectionRequest=@{@"source_id":source,@"list_id":list};NSData *sectionData=[NSJSONSerialization dataWithJSONObject:sectionRequest options:0 error:NULL];NSDictionary *(^readSections)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersListSections(sectionData) options:0 error:NULL];};
 NSCAssert([readSections()[@"sections"] count]==1 && [readSections()[@"sections"][0][@"title"] isEqual:@"Synthetic"],@"exact section read");
 NSData *membershipData=[NSJSONSerialization dataWithJSONObject:@{@"id":task,@"source_id":source,@"list_id":list} options:0 error:NULL];NSDictionary *(^membership)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadSection(membershipData) options:0 error:NULL];};
 FixtureAttachmentContext *attachmentContext=[FixtureAttachmentContext new];attachmentContext.attachments=@[];fixture.attachmentContext=attachmentContext;
 NSDictionary *(^attachmentRead)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadAttachments(membershipData) options:0 error:NULL];};
 NSCAssert([attachmentRead()[@"native_metadata"][@"attachments"] count]==0,@"empty attachment observation");
 FixtureAttachment *file=[FixtureAttachment new];file.remObjectID=Identity(@"REMCDFileAttachment",other);file.accountID=fixture.account.remObjectID;file.reminderID=fixture.remObjectID;file.uti=@"public.data";attachmentContext.attachments=@[file];
 NSCAssert([attachmentRead()[@"native_metadata"][@"attachments"][0][@"kind"] isEqual:@"file"],@"scoped attachment metadata");
 file.accountID=Identity(@"REMCDAccount",other);NSCAssert(attachmentRead()[@"error"],@"foreign attachment refused");file.accountID=fixture.account.remObjectID;
 attachmentContext.attachments=@[file,file];NSCAssert(attachmentRead()[@"error"],@"duplicate attachment refused");attachmentContext.attachments=nil;NSCAssert(attachmentRead()[@"error"],@"unknown attachments refused");
 FixtureAssignments *assignments=[FixtureAssignments new];assignments.assignments=[NSSet set];fixture.assignmentContext=assignments;
 NSDictionary *(^assignment)(void)=^NSDictionary *{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersReadAssignment(membershipData) options:0 error:NULL];};
 NSCAssert(CFGetTypeID((__bridge CFTypeRef)assignment()[@"native_metadata"][@"current_assignment_reported"])==CFBooleanGetTypeID() && [assignment()[@"native_metadata"][@"current_assignment_reported"] boolValue]==NO && [assignment()[@"native_metadata"][@"assignment_record_count"] integerValue]==0,@"empty assignment observation");
 FixtureAssignment *entry=[FixtureAssignment new];entry.accountID=fixture.account.remObjectID;entry.reminderID=fixture.remObjectID;assignments.currentAssignment=entry;NSCAssert(assignment()[@"error"],@"inconsistent assignment refused");assignments.assignments=[NSSet setWithObject:entry];NSCAssert([assignment()[@"native_metadata"][@"current_assignment_reported"] boolValue],@"present assignment observation");entry.accountID=Identity(@"REMCDAccount",other);NSCAssert(assignment()[@"error"],@"foreign assignment refused");entry.accountID=fixture.account.remObjectID;assignments.assignments=nil;NSCAssert(assignment()[@"error"],@"unknown assignments refused");
 NSCAssert([membership()[@"native_metadata"][@"section_status"] isEqual:@"provider_reported"],@"section membership observed");
 section.storage.accountID=Identity(@"REMCDAccount",other);NSCAssert(readSections()[@"error"] && membership()[@"error"],@"foreign section account refused");section.storage.accountID=fixture.account.remObjectID;
 section.list.account.remObjectID=Identity(@"REMCDAccount",other);NSCAssert(membership()[@"error"],@"foreign account membership refused");section.list.account.remObjectID=Identity(@"REMCDAccount",source);section.storage.accountID=fixture.account.remObjectID;
 NSDictionary *rename=@{@"source_id":source,@"list_id":list,@"section_id":other,@"title":@"Renamed",@"expected_sections":readSections()[@"sections"]};NSData *renameData=[NSJSONSerialization dataWithJSONObject:rename options:0 error:NULL];NSDictionary *(^renameSection)(BOOL)=^NSDictionary *(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersRenameSection(renameData,^{return allow;}) options:0 error:NULL];};
 NSCAssert(renameSection(NO)[@"error"] && flagSaves==0,@"closed section rename gate never saves");NSCAssert(renameSection(YES)[@"result"] && flagSaves==1,@"exact section rename dispatches once");flagSaves=0;
 section.storage.displayName=@"Changed";NSCAssert(renameSection(YES)[@"error"] && flagSaves==0,@"stale inventory refuses section rename");section.storage.displayName=@"Synthetic";
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
 NSMutableDictionary *monthly=[earlyAdd mutableCopy];monthly[@"unit"]=@4;monthly[@"count"]=@-1;NSData *monthData=[NSJSONSerialization dataWithJSONObject:monthly options:0 error:NULL];id monthResult=[NSJSONSerialization JSONObjectWithData:ICloudRemindersAddEarly(monthData,^{return NO;}) options:0 error:NULL];NSCAssert([monthResult[@"category"] isEqual:@"native_dispatch_cancelled"] && flagSaves==earlySaves,@"monthly offset stages only before closed gate");
 monthly[@"unit"]=@5;monthData=[NSJSONSerialization dataWithJSONObject:monthly options:0 error:NULL];NSCAssert([NSJSONSerialization JSONObjectWithData:ICloudRemindersAddEarly(monthData,^{return YES;}) options:0 error:NULL][@"error"] && flagSaves==earlySaves,@"unknown native interval refused");
 NSCAssert(addEarly(NO)[@"error"] && flagSaves==earlySaves,@"closed early gate never saves");
 NSCAssert(addEarly(YES)[@"result"] && flagSaves==earlySaves+1,@"one fixed early write saves only after gate");
 fixture.storage.hasUnfetchedDueDateDeltaAlerts=YES;NSCAssert(addEarly(YES)[@"error"] && flagSaves==earlySaves+1,@"unavailable early state refuses save");fixture.storage.hasUnfetchedDueDateDeltaAlerts=NO;flagSaves=0;
 payload[@"dueDateDeltaAlerts"]=@[@{@"identifier":other,@"dueDateDeltaUnit":@0,@"dueDateDeltaCount":@-5}];fixture.storage.dueDateDeltaAlertsData=[NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];
 NSMutableDictionary *clearRequest=[@{@"id":task,@"source_id":source,@"list_id":list,@"expected_fingerprint":early()[@"native_metadata"][@"early_payload_fingerprint"]} mutableCopy];
 NSDictionary *(^clearEarly)(BOOL)=^NSDictionary *(BOOL allow){NSData *data=[NSJSONSerialization dataWithJSONObject:clearRequest options:0 error:NULL];return [NSJSONSerialization JSONObjectWithData:ICloudRemindersClearEarly(data,^{return allow;}) options:0 error:NULL];};
 NSCAssert(clearEarly(NO)[@"error"] && flagSaves==0 && fixture.storage.dueDateDeltaAlertsData!=nil,@"closed clear gate cannot save");
 NSString *clearFingerprint=clearRequest[@"expected_fingerprint"];clearRequest[@"expected_fingerprint"]=[@"f" stringByPaddingToLength:64 withString:@"f" startingAtIndex:0];
 NSCAssert(clearEarly(YES)[@"error"] && flagSaves==0,@"changed complete early payload refuses save");clearRequest[@"expected_fingerprint"]=clearFingerprint;
 saveFails=YES;NSCAssert([clearEarly(YES)[@"mutation_dispatched"] boolValue] && flagSaves==1 && fixture.storage.dueDateDeltaAlertsData!=nil,@"failed save remains dispatched and uncertain");saveFails=NO;flagSaves=0;
 NSCAssert(clearEarly(YES)[@"result"] && flagSaves==1 && fixture.storage.dueDateDeltaAlertsData==nil,@"one exact reviewed early alert removed using native UUID");
 NSCAssert(clearEarly(YES)[@"error"] && flagSaves==1,@"repeated or already cleared request cannot save");flagSaves=0;
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
    for real,fake in [('REMAssignment','FixtureAssignment'),('REMDueDateDeltaInterval','FixtureDelta'),('REMStore','FixtureStore'),('REMListsDataView','FixtureListsView'),('REMObjectID','FixtureID'),('REMReminder','FixtureReminder'),('REMSaveRequest','FixtureSave'),('REMListSectionsDataView','FixtureSectionView')]:source=source.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
    with tempfile.TemporaryDirectory(prefix='native-flag-contract-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(source+'\n'+FIXTURE)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['actual_fixed_read_and_runtime_signatures','true_and_false_integer_flag','exact_task_list_source_and_account_relation','nil_unknown_or_denied_not_empty_success','bounded_tags_and_exact_account_task_identity','hierarchy_parent_scope_and_unknown_refusal','bounded_direct_children_and_scope_refusals','fixed_unsaved_gate_and_synthetic_save_outcomes','no_arbitrary_selectors']}))
if __name__=='__main__':run()
