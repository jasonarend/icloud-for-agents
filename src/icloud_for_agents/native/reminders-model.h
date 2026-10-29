#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
// Called only after the public EventKit lookup has proved exact source/list/ID scope.
FOUNDATION_EXPORT NSData *ICloudRemindersReadFlag(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadUrgent(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersSetFlag(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersSetUrgent(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersRemoveTag(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersAddTag(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersReplaceTag(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersReadTags(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadHierarchy(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadChildren(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersListSections(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadListFeatures(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadSection(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadEarly(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadAssignment(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadAttachments(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersListTemplates(NSData *request);
FOUNDATION_EXPORT NSData *ICloudRemindersListSmartLists(NSData *request);
FOUNDATION_EXPORT NSData *ICloudRemindersReadListParent(NSData *request);
FOUNDATION_EXPORT NSData *ICloudRemindersGroupLists(NSData *request);
FOUNDATION_EXPORT NSData *ICloudRemindersTemplateSections(NSData *request);
FOUNDATION_EXPORT NSData *ICloudRemindersAddEarly(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersClearEarly(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersCreateSection(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersRenameSection(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersCreateSubtask(NSData *request,BOOL (^enterMutation)(void));
// Exact selected-account recovery-view probe, not a restore or deletion operation.
FOUNDATION_EXPORT NSData *ICloudRemindersRecoveryLookup(NSData *identity);
FOUNDATION_EXPORT NSData *ICloudRemindersRecoveryTree(NSData *identity);
FOUNDATION_EXPORT NSData *ICloudRemindersRecoveryList(NSData *request);
FOUNDATION_EXPORT NSData *ICloudRemindersListHierarchy(NSData *request);
FOUNDATION_EXPORT NSData *ICloudRemindersMoveSubtree(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersSetListPinned(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersRemoveRootLeaf(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersRestoreRootLeaf(NSData *request,BOOL (^enterMutation)(void));
NSData *ICloudRemindersSetListBadge(NSData *data, BOOL (^enterMutation)(void));
NSData *ICloudRemindersListOrder(NSData *data);
NSData *ICloudRemindersReorderRoot(NSData *data, BOOL (^enterMutation)(void));
NSData *ICloudRemindersGroupInventory(NSData *data);
NSData *ICloudRemindersCreateRootGroup(NSData *data, BOOL (^enterMutation)(void));
NS_ASSUME_NONNULL_END
