// Optional fixed ReminderKit read. No save request, SQL, body projection or selectors from JSON.
#import "reminders-model.h"
#import <objc/runtime.h>
#import <objc/message.h>
#import <dlfcn.h>
static NSData *EncodeFlag(NSDictionary *value){return [NSJSONSerialization dataWithJSONObject:value options:0 error:NULL] ?: [NSData data];}
static NSDictionary *FlagFailure(NSString *category){return @{@"protocol_version":@1,@"error":@"private_metadata_unavailable",@"category":category,@"data_mutated":@NO};}
static NSString *Signature(Method method){
    if(!method)return @"";
    NSString *raw=[NSString stringWithUTF8String:method_getTypeEncoding(method)];
    return [[raw componentsSeparatedByCharactersInSet:NSCharacterSet.decimalDigitCharacterSet] componentsJoinedByString:@""];
}
static BOOL MethodMatches(Class cls,NSString *name,NSString *expected,BOOL meta){return cls && [Signature(meta?class_getClassMethod(cls,NSSelectorFromString(name)):class_getInstanceMethod(cls,NSSelectorFromString(name))) isEqual:expected];}
static BOOL FlagReadInterface(void){
    if(NSProcessInfo.processInfo.operatingSystemVersion.majorVersion!=27)return NO;
    if(!dlopen("/System/Library/PrivateFrameworks/ReminderKit.framework/ReminderKit",RTLD_LAZY|RTLD_LOCAL))return NO;
    return MethodMatches(NSClassFromString(@"REMObjectID"),@"objectIDWithURL:",@"@@:@",YES) &&
      MethodMatches(NSClassFromString(@"REMStore"),@"fetchReminderWithObjectID:error:",@"@@:@^@",NO) &&
      MethodMatches(NSClassFromString(@"REMReminder"),@"remObjectID",@"@@:",NO) &&
      MethodMatches(NSClassFromString(@"REMReminder"),@"list",@"@@:",NO) &&
      MethodMatches(NSClassFromString(@"REMReminder"),@"account",@"@@:",NO) &&
      MethodMatches(NSClassFromString(@"REMReminder"),@"flaggedContext",@"@@:",NO);
}
static id ObjectValue(id object,NSString *name){
    if(!object || !MethodMatches([object class],name,@"@@:",NO))return nil;
    return ((id(*)(id,SEL))objc_msgSend)(object,NSSelectorFromString(name));
}
static NSString *ObjectUUID(id object,NSString *entity){
    id identity=ObjectValue(object,@"remObjectID");id name=ObjectValue(identity,@"entityName");id uuid=ObjectValue(identity,@"uuid");
    if(![name isEqual:entity] || ![uuid isKindOfClass:NSUUID.class])return nil;
    return [uuid UUIDString];
}
static BOOL UUIDEqual(NSString *a,NSString *b){return a && b && [a caseInsensitiveCompare:b]==NSOrderedSame;}
static id SelectedFailure(NSDictionary **failure,NSString *category){*failure=FlagFailure(category);return nil;}
static id SelectedReminder(NSData *data,NSDictionary **failureResult){
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![identity isKindOfClass:NSDictionary.class] || identity.count!=3)return SelectedFailure(failureResult,@"invalid_public_identity");
        for(NSString *key in @[@"id",@"source_id",@"list_id"]){
            id value=identity[key];NSUUID *uuid=[value isKindOfClass:NSString.class]?[[NSUUID alloc]initWithUUIDString:value]:nil;
            if(!uuid || !UUIDEqual(uuid.UUIDString,value))return SelectedFailure(failureResult,@"unsupported_public_identity");
        }
        if(!FlagReadInterface())return SelectedFailure(failureResult,@"unsupported_interface");
        Class storeClass=NSClassFromString(@"REMStore"),objectClass=NSClassFromString(@"REMObjectID");
        id store=[storeClass new];NSURL *url=[NSURL URLWithString:[@"x-apple-reminderkit://REMCDReminder/" stringByAppendingString:identity[@"id"]]];
        id objectID=((id(*)(id,SEL,id))objc_msgSend)((id)objectClass,NSSelectorFromString(@"objectIDWithURL:"),url);
        if(!store || !objectID)return SelectedFailure(failureResult,@"private_store_unavailable");
        NSError *error=nil;id reminder=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchReminderWithObjectID:error:"),objectID,&error);
        if(error){NSMutableDictionary *failure=[FlagFailure(@"private_read_denied_or_unavailable") mutableCopy];NSSet *domains=[NSSet setWithArray:@[@"com.apple.reminderkit",@"NSCocoaErrorDomain",@"NSPOSIXErrorDomain",@"EKErrorDomain",@"NSOSStatusErrorDomain"]];failure[@"native_error_domain"]=[domains containsObject:error.domain]?error.domain:@"other";failure[@"native_error_code"]=@(error.code);*failureResult=failure;return nil;}
        if(!reminder)return SelectedFailure(failureResult,@"private_reminder_not_found");
        id list=ObjectValue(reminder,@"list"),account=ObjectValue(reminder,@"account"),listAccount=ObjectValue(list,@"account");
        if(!UUIDEqual(ObjectUUID(reminder,@"REMCDReminder"),identity[@"id"]) || !UUIDEqual(ObjectUUID(list,@"REMCDList"),identity[@"list_id"]) ||
           !UUIDEqual(ObjectUUID(account,@"REMCDAccount"),identity[@"source_id"]) || !UUIDEqual(ObjectUUID(listAccount,@"REMCDAccount"),identity[@"source_id"]))return SelectedFailure(failureResult,@"native_identity_mismatch");
        return reminder;
}
NSData *ICloudRemindersReadFlag(NSData *data){
    @try{
        NSDictionary *failure=nil;id reminder=SelectedReminder(data,&failure);
        if(!reminder)return EncodeFlag(failure);
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        id context=ObjectValue(reminder,@"flaggedContext");
        if(!context || !MethodMatches([context class],@"flagged",@"q@:",NO))return EncodeFlag(FlagFailure(@"unsupported_flag_metadata"));
        NSInteger flag=((NSInteger(*)(id,SEL))objc_msgSend)(context,NSSelectorFromString(@"flagged"));
        if(flag!=0 && flag!=1)return EncodeFlag(FlagFailure(@"unknown_flag_state"));
        return EncodeFlag(@{@"protocol_version":@1,@"native_metadata":@{@"backend":@"reminderkit-flags-v1",@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"flagged":@((BOOL)(flag==1)),@"flag_state":@(flag),@"coverage":@[@"flagged"],@"native_metadata_complete":@NO,@"observation_consistency":@"separate_provider_reads"}});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}

// Tags are read only from the exact reminder already proved by EventKit.
NSData *ICloudRemindersReadTags(NSData *data){
    @try{
        NSDictionary *failure=nil;id reminder=SelectedReminder(data,&failure);
        if(!reminder)return EncodeFlag(failure);
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        id context=ObjectValue(reminder,@"hashtagContext"),collection=ObjectValue(context,@"hashtags");
        NSArray *values=nil;
        if([collection isKindOfClass:NSArray.class])values=collection;
        else if([collection isKindOfClass:NSSet.class])values=[collection allObjects];
        else if([collection isKindOfClass:NSOrderedSet.class])values=[collection array];
        if(!values || values.count>200)return EncodeFlag(FlagFailure(@"unsupported_tag_metadata"));
        NSMutableArray *tags=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];
        for(id tag in values){
            NSString *identifier=ObjectUUID(tag,@"REMCDHashtag");id name=ObjectValue(tag,@"name");
            id accountID=ObjectValue(tag,@"accountID"),reminderID=ObjectValue(tag,@"reminderID");
            id accountUUID=ObjectValue(accountID,@"uuid"),reminderUUID=ObjectValue(reminderID,@"uuid");
            if(!identifier || [seen containsObject:identifier] || ![name isKindOfClass:NSString.class] || ![name length] || [name lengthOfBytesUsingEncoding:NSUTF8StringEncoding]>512 ||
               ![ObjectValue(accountID,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(reminderID,@"entityName") isEqual:@"REMCDReminder"] ||
               ![accountUUID isKindOfClass:NSUUID.class] || ![reminderUUID isKindOfClass:NSUUID.class] ||
               !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([reminderUUID UUIDString],identity[@"id"]) ||
               !MethodMatches([tag class],@"type",@"q@:",NO))return EncodeFlag(FlagFailure(@"unsupported_tag_metadata"));
            NSInteger kind=((NSInteger(*)(id,SEL))objc_msgSend)(tag,NSSelectorFromString(@"type"));
            [seen addObject:identifier];[tags addObject:@{@"id":identifier,@"name":name,@"native_type":@(kind)}];
        }
        [tags sortUsingComparator:^NSComparisonResult(NSDictionary *a,NSDictionary *b){return [a[@"id"] compare:b[@"id"]];}];
        return EncodeFlag(@{@"protocol_version":@1,@"native_metadata":@{@"backend":@"reminderkit-tags-v1",@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"tags":tags,@"tags_complete":@YES,@"coverage":@[@"tags"],@"native_metadata_complete":@NO,@"observation_consistency":@"separate_provider_reads"}});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}

// The provider's parent and child-presence hints are not a complete subtree inventory.
NSData *ICloudRemindersReadHierarchy(NSData *data){
    @try{
        NSDictionary *failure=nil;id reminder=SelectedReminder(data,&failure);
        if(!reminder)return EncodeFlag(failure);
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(!MethodMatches([reminder class],@"isSubtask",@"B@:",NO) || !MethodMatches([reminder class],@"parentReminder",@"@@:",NO) || !MethodMatches([reminder class],@"subtaskContext",@"@@:",NO))return EncodeFlag(FlagFailure(@"unsupported_hierarchy_metadata"));
        BOOL subtask=((BOOL(*)(id,SEL))objc_msgSend)(reminder,NSSelectorFromString(@"isSubtask"));
        id parent=ObjectValue(reminder,@"parentReminder"),context=ObjectValue(reminder,@"subtaskContext");
        if(subtask && !parent)return EncodeFlag(FlagFailure(@"hierarchy_parent_unavailable"));
        if(!subtask && parent)return EncodeFlag(FlagFailure(@"hierarchy_parent_inconsistent"));
        id parentID=[NSNull null];
        if(parent){
            NSString *identifier=ObjectUUID(parent,@"REMCDReminder");id list=ObjectValue(parent,@"list"),account=ObjectValue(parent,@"account"),listAccount=ObjectValue(list,@"account");
            if(!identifier || UUIDEqual(identifier,identity[@"id"]) || !UUIDEqual(ObjectUUID(list,@"REMCDList"),identity[@"list_id"]) ||
               !UUIDEqual(ObjectUUID(account,@"REMCDAccount"),identity[@"source_id"]) || !UUIDEqual(ObjectUUID(listAccount,@"REMCDAccount"),identity[@"source_id"]))return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
            parentID=identifier;
        }
        id hasChildren=[NSNull null];NSString *childrenStatus=@"unavailable";
        if(context){
            if(!MethodMatches([context class],@"hasSubtasksWithError:",@"B@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_hierarchy_metadata"));
            NSError *error=nil;BOOL result=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(context,NSSelectorFromString(@"hasSubtasksWithError:"),&error);
            if(error)return EncodeFlag(FlagFailure(@"hierarchy_read_unavailable"));
            hasChildren=@(result);childrenStatus=@"provider_reported";
        }
        return EncodeFlag(@{@"protocol_version":@1,@"native_metadata":@{@"backend":@"reminderkit-hierarchy-v1",@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"is_subtask":@(subtask),@"parent_id":parentID,@"has_subtasks":hasChildren,@"hierarchy_complete":@NO,@"children_status":childrenStatus,@"coverage":@[@"hierarchy"],@"native_metadata_complete":@NO,@"observation_consistency":@"separate_provider_reads"}});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}

// Direct children are a bounded provider view, not a complete descendant inventory.
NSData *ICloudRemindersReadChildren(NSData *data){
    @try{
        NSDictionary *failure=nil;id reminder=SelectedReminder(data,&failure);
        if(!reminder)return EncodeFlag(failure);
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(!MethodMatches([reminder class],@"subtaskContext",@"@@:",NO))return EncodeFlag(FlagFailure(@"unsupported_children_metadata"));
        id context=ObjectValue(reminder,@"subtaskContext"),children=[NSNull null];NSString *status=@"unavailable";
        if(context){
            if(!MethodMatches([context class],@"fetchRemindersWithError:",@"@@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_children_metadata"));
            NSError *error=nil;id collection=((id(*)(id,SEL,NSError **))objc_msgSend)(context,NSSelectorFromString(@"fetchRemindersWithError:"),&error);
            if(error)return EncodeFlag(FlagFailure(@"children_read_unavailable"));
            NSArray *values=nil;
            if([collection isKindOfClass:NSArray.class])values=collection;
            else if([collection isKindOfClass:NSSet.class])values=[collection allObjects];
            else if([collection isKindOfClass:NSOrderedSet.class])values=[collection array];
            if(!values || values.count>100)return EncodeFlag(FlagFailure(@"unsupported_children_metadata"));
            NSMutableArray *ids=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];
            for(id child in values){
                NSString *identifier=ObjectUUID(child,@"REMCDReminder");id parent=ObjectValue(child,@"parentReminder"),list=ObjectValue(child,@"list"),account=ObjectValue(child,@"account"),listAccount=ObjectValue(list,@"account");
                if(!identifier || UUIDEqual(identifier,identity[@"id"]) || [seen containsObject:identifier] || !UUIDEqual(ObjectUUID(parent,@"REMCDReminder"),identity[@"id"]) ||
                   !UUIDEqual(ObjectUUID(list,@"REMCDList"),identity[@"list_id"]) || !UUIDEqual(ObjectUUID(account,@"REMCDAccount"),identity[@"source_id"]) || !UUIDEqual(ObjectUUID(listAccount,@"REMCDAccount"),identity[@"source_id"]) ||
                   !MethodMatches([child class],@"isSubtask",@"B@:",NO) || !((BOOL(*)(id,SEL))objc_msgSend)(child,NSSelectorFromString(@"isSubtask")))return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
                [seen addObject:identifier];[ids addObject:identifier];
            }
            [ids sortUsingSelector:@selector(compare:)];children=ids;status=@"provider_reported";
        }
        return EncodeFlag(@{@"protocol_version":@1,@"native_metadata":@{@"backend":@"reminderkit-children-v1",@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"child_ids":children,@"children_fetch_status":status,@"children_complete":@NO,@"coverage":@[@"children"],@"native_metadata_complete":@NO,@"observation_consistency":@"separate_provider_reads"}});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}
