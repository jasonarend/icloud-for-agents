#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
FOUNDATION_EXPORT NSData *ICloudNotesModelStatus(void);
FOUNDATION_EXPORT NSData *ICloudNotesStoreStatus(void);
FOUNDATION_EXPORT NSData *ICloudNotesModelRename(NSData *request, BOOL (^enterMutation)(void));
FOUNDATION_EXPORT NSData *ICloudNotesModelFolderRole(NSData *request);
NS_ASSUME_NONNULL_END
