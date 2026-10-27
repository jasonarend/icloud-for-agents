#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
// Called only after the public EventKit lookup has proved exact source/list/ID scope.
FOUNDATION_EXPORT NSData *ICloudRemindersReadFlag(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersSetFlag(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersRemoveTag(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersAddTag(NSData *request,BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudRemindersReadTags(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadHierarchy(NSData *publicIdentity);
FOUNDATION_EXPORT NSData *ICloudRemindersReadChildren(NSData *publicIdentity);
// Exact selected-account recovery-view probe, not a restore or deletion operation.
FOUNDATION_EXPORT NSData *ICloudRemindersRecoveryLookup(NSData *identity);
FOUNDATION_EXPORT NSData *ICloudRemindersRecoveryTree(NSData *identity);
NS_ASSUME_NONNULL_END
