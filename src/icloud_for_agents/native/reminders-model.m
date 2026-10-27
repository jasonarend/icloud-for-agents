// Optional fixed ReminderKit reads and gated single-flag/tag saves. No SQL, body projection or selectors from JSON.
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
static id SelectedReminderWithStore(NSData *data,NSDictionary **failureResult,id __strong *storeResult){
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
        if(storeResult)*storeResult=store;
        return reminder;
}
static id SelectedReminder(NSData *data,NSDictionary **failureResult){return SelectedReminderWithStore(data,failureResult,NULL);}
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

// Read the current user's native urgent state; never infer it from priority or flag.
NSData *ICloudRemindersReadUrgent(NSData *data){
    @try{
        NSDictionary *failure=nil;id reminder=SelectedReminder(data,&failure);if(!reminder)return EncodeFlag(failure);
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        id context=ObjectValue(reminder,@"urgentAlarmContext");
        if(!context || !MethodMatches([context class],@"isUrgentStateEnabledForCurrentUser",@"B@:",NO))return EncodeFlag(FlagFailure(@"unsupported_urgent_metadata"));
        BOOL urgent=((BOOL(*)(id,SEL))objc_msgSend)(context,NSSelectorFromString(@"isUrgentStateEnabledForCurrentUser"));
        return EncodeFlag(@{@"protocol_version":@1,@"native_metadata":@{@"backend":@"reminderkit-urgent-v1",@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"urgent":@((BOOL)urgent),@"coverage":@[@"urgent"],@"native_metadata_complete":@NO,@"observation_consistency":@"separate_provider_reads"}});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}

// Fixed single-field native flag write; no caller-supplied selector or bulk mutation.
NSData *ICloudRemindersSetFlag(NSData *data,BOOL (^enterMutation)(void)){
    __block BOOL dispatched=NO;
    @try{
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=5 || !enterMutation)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        id requested=request[@"flagged"],prior=request[@"expected_flagged"];
        if(![requested isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)requested)!=CFBooleanGetTypeID() ||
           ![prior isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)prior)!=CFBooleanGetTypeID())return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSDictionary *identity=@{@"id":request[@"id"] ?: [NSNull null],@"source_id":request[@"source_id"] ?: [NSNull null],@"list_id":request[@"list_id"] ?: [NSNull null]};
        NSData *identityData=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];NSDictionary *failure=nil;id store=nil;
        id reminder=SelectedReminderWithStore(identityData,&failure,&store);
        if(!reminder || !store)return EncodeFlag(failure ?: FlagFailure(@"private_store_unavailable"));
        id current=ObjectValue(reminder,@"flaggedContext");
        if(!current || !MethodMatches([current class],@"flagged",@"q@:",NO))return EncodeFlag(FlagFailure(@"unsupported_flag_metadata"));
        NSInteger currentFlag=((NSInteger(*)(id,SEL))objc_msgSend)(current,NSSelectorFromString(@"flagged"));
        if((currentFlag!=0 && currentFlag!=1) || currentFlag!=[prior integerValue])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        Class cls=NSClassFromString(@"REMSaveRequest");
        if(!MethodMatches(cls,@"initWithStore:",@"@@:@",NO) || !MethodMatches(cls,@"updateReminder:",@"@@:@",NO) || !MethodMatches(cls,@"saveSynchronouslyWithError:",@"B@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_flag_write"));
        id save=((id(*)(id,SEL,id))objc_msgSend)([cls alloc],NSSelectorFromString(@"initWithStore:"),store);
        id change=((id(*)(id,SEL,id))objc_msgSend)(save,NSSelectorFromString(@"updateReminder:"),reminder);
        id flag=ObjectValue(change,@"flaggedContext");
        if(!flag || !MethodMatches([flag class],@"setFlagged:",@"v@:q",NO))return EncodeFlag(FlagFailure(@"unsupported_flag_write"));
        ((void(*)(id,SEL,NSInteger))objc_msgSend)(flag,NSSelectorFromString(@"setFlagged:"),[requested boolValue]?1:0);
        if(!enterMutation())return EncodeFlag(FlagFailure(@"native_dispatch_cancelled"));
        dispatched=YES;NSError *error=nil;
        BOOL saved=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(save,NSSelectorFromString(@"saveSynchronouslyWithError:"),&error);
        if(!saved || error)return EncodeFlag(@{@"protocol_version":@1,@"error":@"mutation_failed_or_uncertain",@"mutation_dispatched":@YES});
        return EncodeFlag(@{@"protocol_version":@1,@"result":@{@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"saved_locally":@YES}});
    }@catch(NSException *exception){return EncodeFlag(@{@"protocol_version":@1,@"error":dispatched?@"mutation_failed_or_uncertain":@"private_metadata_unavailable",@"mutation_dispatched":@(dispatched)});}
}

// Change only the current user urgent state after an exact prior-state check.
NSData *ICloudRemindersSetUrgent(NSData *data,BOOL (^enterMutation)(void)){
    __block BOOL dispatched=NO;
    @try{
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=5 || !enterMutation)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        id requested=request[@"urgent"],prior=request[@"expected_urgent"];
        if(![requested isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)requested)!=CFBooleanGetTypeID() ||
           ![prior isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)prior)!=CFBooleanGetTypeID())return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSDictionary *identity=@{@"id":request[@"id"] ?: [NSNull null],@"source_id":request[@"source_id"] ?: [NSNull null],@"list_id":request[@"list_id"] ?: [NSNull null]};
        NSData *identityData=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];NSDictionary *failure=nil;id store=nil;
        id reminder=SelectedReminderWithStore(identityData,&failure,&store);
        if(!reminder || !store)return EncodeFlag(failure ?: FlagFailure(@"private_store_unavailable"));
        id current=ObjectValue(reminder,@"urgentAlarmContext");
        if(!current || !MethodMatches([current class],@"isUrgentStateEnabledForCurrentUser",@"B@:",NO))return EncodeFlag(FlagFailure(@"unsupported_urgent_metadata"));
        BOOL currentFlag=((BOOL(*)(id,SEL))objc_msgSend)(current,NSSelectorFromString(@"isUrgentStateEnabledForCurrentUser"));
        if(currentFlag!=[prior boolValue])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        Class cls=NSClassFromString(@"REMSaveRequest");
        if(!MethodMatches(cls,@"initWithStore:",@"@@:@",NO) || !MethodMatches(cls,@"updateReminder:",@"@@:@",NO) || !MethodMatches(cls,@"saveSynchronouslyWithError:",@"B@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_urgent_write"));
        id save=((id(*)(id,SEL,id))objc_msgSend)([cls alloc],NSSelectorFromString(@"initWithStore:"),store);
        id change=((id(*)(id,SEL,id))objc_msgSend)(save,NSSelectorFromString(@"updateReminder:"),reminder);
        id flag=ObjectValue(change,@"urgentAlarmContext");
        if(!flag || !MethodMatches([flag class],@"setIsUrgentStateEnabledForCurrentUser:",@"v@:B",NO))return EncodeFlag(FlagFailure(@"unsupported_urgent_write"));
        ((void(*)(id,SEL,BOOL))objc_msgSend)(flag,NSSelectorFromString(@"setIsUrgentStateEnabledForCurrentUser:"),[requested boolValue]);
        if(!enterMutation())return EncodeFlag(FlagFailure(@"native_dispatch_cancelled"));
        dispatched=YES;NSError *error=nil;
        BOOL saved=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(save,NSSelectorFromString(@"saveSynchronouslyWithError:"),&error);
        if(!saved || error)return EncodeFlag(@{@"protocol_version":@1,@"error":@"mutation_failed_or_uncertain",@"mutation_dispatched":@YES});
        return EncodeFlag(@{@"protocol_version":@1,@"result":@{@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"saved_locally":@YES}});
    }@catch(NSException *exception){return EncodeFlag(@{@"protocol_version":@1,@"error":dispatched?@"mutation_failed_or_uncertain":@"private_metadata_unavailable",@"mutation_dispatched":@(dispatched)});}
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

// Remove one exact native tag relationship; retain every other observed tag ID.
NSData *ICloudRemindersRemoveTag(NSData *data,BOOL (^enterMutation)(void)){
    __block BOOL dispatched=NO;
    @try{
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=5 || !enterMutation)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSString *tagID=request[@"tag_id"];NSArray *expected=request[@"expected_tags"];
        if(![tagID isKindOfClass:NSString.class] || ![[NSUUID alloc]initWithUUIDString:tagID] || ![expected isKindOfClass:NSArray.class] || expected.count<1 || expected.count>200)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSDictionary *identity=@{@"id":request[@"id"] ?: [NSNull null],@"source_id":request[@"source_id"] ?: [NSNull null],@"list_id":request[@"list_id"] ?: [NSNull null]};
        NSData *identityData=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];NSDictionary *failure=nil;id store=nil;
        id reminder=SelectedReminderWithStore(identityData,&failure,&store);if(!reminder || !store)return EncodeFlag(failure ?: FlagFailure(@"private_store_unavailable"));
        id observed=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadTags(identityData) options:0 error:NULL];
        if(![observed[@"native_metadata"][@"tags"] isEqual:expected])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        Class cls=NSClassFromString(@"REMSaveRequest");
        if(!MethodMatches(cls,@"initWithStore:",@"@@:@",NO) || !MethodMatches(cls,@"updateReminder:",@"@@:@",NO) || !MethodMatches(cls,@"saveSynchronouslyWithError:",@"B@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_tag_write"));
        id save=((id(*)(id,SEL,id))objc_msgSend)([cls alloc],NSSelectorFromString(@"initWithStore:"),store);
        id change=((id(*)(id,SEL,id))objc_msgSend)(save,NSSelectorFromString(@"updateReminder:"),reminder),context=ObjectValue(change,@"hashtagContext"),collection=ObjectValue(context,@"hashtags");
        if(!context || !MethodMatches([context class],@"removeHashtag:",@"v@:@",NO))return EncodeFlag(FlagFailure(@"unsupported_tag_write"));
        NSArray *(^array)(id)=^NSArray *(id value){if([value isKindOfClass:NSArray.class])return value;if([value isKindOfClass:NSSet.class])return [value allObjects];if([value isKindOfClass:NSOrderedSet.class])return [value array];return nil;};
        NSArray *values=array(collection);if(!values || values.count!=expected.count)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        NSMutableSet *seen=[NSMutableSet set];id target=nil;
        for(id tag in values){
            NSString *identifier=ObjectUUID(tag,@"REMCDHashtag");id name=ObjectValue(tag,@"name"),accountID=ObjectValue(tag,@"accountID"),reminderID=ObjectValue(tag,@"reminderID");
            id accountUUID=ObjectValue(accountID,@"uuid"),reminderUUID=ObjectValue(reminderID,@"uuid");
            if(!identifier || [seen containsObject:identifier] || ![name isKindOfClass:NSString.class] || ![accountUUID isKindOfClass:NSUUID.class] || ![reminderUUID isKindOfClass:NSUUID.class]
                    || ![ObjectValue(accountID,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(reminderID,@"entityName") isEqual:@"REMCDReminder"]
                    || !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([reminderUUID UUIDString],identity[@"id"]) || !MethodMatches([tag class],@"type",@"q@:",NO))return EncodeFlag(FlagFailure(@"snapshot_changed"));
            NSInteger kind=((NSInteger(*)(id,SEL))objc_msgSend)(tag,NSSelectorFromString(@"type"));
            if(![expected containsObject:@{@"id":identifier,@"name":name,@"native_type":@(kind)}])return EncodeFlag(FlagFailure(@"snapshot_changed"));
            [seen addObject:identifier];if(UUIDEqual(identifier,tagID)){if(target)return EncodeFlag(FlagFailure(@"snapshot_changed"));target=tag;}
        }
        if(!target)return EncodeFlag(FlagFailure(@"not_found_in_scope"));
        NSString *targetIdentity=ObjectUUID(target,@"REMCDHashtag");
        ((void(*)(id,SEL,id))objc_msgSend)(context,NSSelectorFromString(@"removeHashtag:"),target);
        NSArray *staged=array(ObjectValue(context,@"hashtags"));if(!staged || staged.count+1!=expected.count)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        NSMutableSet *remaining=[NSMutableSet set];for(id tag in staged){
            NSString *identifier=ObjectUUID(tag,@"REMCDHashtag");id name=ObjectValue(tag,@"name"),accountID=ObjectValue(tag,@"accountID"),reminderID=ObjectValue(tag,@"reminderID");
            id accountUUID=ObjectValue(accountID,@"uuid"),reminderUUID=ObjectValue(reminderID,@"uuid");
            if(!identifier || [remaining containsObject:identifier] || ![name isKindOfClass:NSString.class] || ![accountUUID isKindOfClass:NSUUID.class] || ![reminderUUID isKindOfClass:NSUUID.class]
                    || ![ObjectValue(accountID,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(reminderID,@"entityName") isEqual:@"REMCDReminder"]
                    || !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([reminderUUID UUIDString],identity[@"id"]) || !MethodMatches([tag class],@"type",@"q@:",NO))return EncodeFlag(FlagFailure(@"snapshot_changed"));
            NSInteger kind=((NSInteger(*)(id,SEL))objc_msgSend)(tag,NSSelectorFromString(@"type"));
            if(![expected containsObject:@{@"id":identifier,@"name":name,@"native_type":@(kind)}])return EncodeFlag(FlagFailure(@"snapshot_changed"));
            [remaining addObject:identifier];
        }
        [seen removeObject:targetIdentity];if(![seen isEqual:remaining])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        if(!enterMutation())return EncodeFlag(FlagFailure(@"native_dispatch_cancelled"));
        dispatched=YES;NSError *error=nil;BOOL saved=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(save,NSSelectorFromString(@"saveSynchronouslyWithError:"),&error);
        if(!saved || error)return EncodeFlag(@{@"protocol_version":@1,@"error":@"mutation_failed_or_uncertain",@"mutation_dispatched":@YES});
        return EncodeFlag(@{@"protocol_version":@1,@"result":@{@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"saved_locally":@YES}});
    }@catch(NSException *exception){return EncodeFlag(@{@"protocol_version":@1,@"error":dispatched?@"mutation_failed_or_uncertain":@"private_metadata_unavailable",@"mutation_dispatched":@(dispatched)});}
}

// Stage one fixed native tag; dispatch remains guarded and readback is mandatory.
NSData *ICloudRemindersAddTag(NSData *data,BOOL (^enterMutation)(void)){
    __block BOOL dispatched=NO;
    @try{
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=5 || !enterMutation)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSString *tagName=request[@"tag_name"];NSArray *expected=request[@"expected_tags"];
        if(![tagName isKindOfClass:NSString.class] || tagName.length<1 || tagName.length>200 || [tagName lengthOfBytesUsingEncoding:NSUTF8StringEncoding]>512 || ![expected isKindOfClass:NSArray.class] || expected.count>=200)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSMutableCharacterSet *allowed=[[NSCharacterSet alphanumericCharacterSet] mutableCopy];[allowed formUnionWithCharacterSet:[NSCharacterSet nonBaseCharacterSet]];[allowed addCharactersInString:@"_"];
        if([tagName rangeOfCharacterFromSet:allowed.invertedSet].location!=NSNotFound || [[NSCharacterSet nonBaseCharacterSet] characterIsMember:[tagName characterAtIndex:0]])return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSDictionary *identity=@{@"id":request[@"id"] ?: [NSNull null],@"source_id":request[@"source_id"] ?: [NSNull null],@"list_id":request[@"list_id"] ?: [NSNull null]};
        NSData *identityData=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];NSDictionary *failure=nil;id store=nil;
        id reminder=SelectedReminderWithStore(identityData,&failure,&store);if(!reminder || !store)return EncodeFlag(failure ?: FlagFailure(@"private_store_unavailable"));
        id observed=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadTags(identityData) options:0 error:NULL];
        if(![observed[@"native_metadata"][@"tags"] isEqual:expected])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        Class cls=NSClassFromString(@"REMSaveRequest");
        if(!MethodMatches(cls,@"initWithStore:",@"@@:@",NO) || !MethodMatches(cls,@"updateReminder:",@"@@:@",NO) || !MethodMatches(cls,@"saveSynchronouslyWithError:",@"B@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_tag_write"));
        id save=((id(*)(id,SEL,id))objc_msgSend)([cls alloc],NSSelectorFromString(@"initWithStore:"),store);
        id change=((id(*)(id,SEL,id))objc_msgSend)(save,NSSelectorFromString(@"updateReminder:"),reminder),context=ObjectValue(change,@"hashtagContext"),collection=ObjectValue(context,@"hashtags");
        if(!context || !MethodMatches([context class],@"addHashtagWithType:name:",@"@@:q@",NO))return EncodeFlag(FlagFailure(@"unsupported_tag_write"));
        NSArray *(^array)(id)=^NSArray *(id value){if([value isKindOfClass:NSArray.class])return value;if([value isKindOfClass:NSSet.class])return [value allObjects];if([value isKindOfClass:NSOrderedSet.class])return [value array];return nil;};
        NSArray *values=array(collection);if(!values || values.count!=expected.count)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        NSMutableSet *seen=[NSMutableSet set];
        for(id tag in values){
            NSString *identifier=ObjectUUID(tag,@"REMCDHashtag");id name=ObjectValue(tag,@"name"),accountID=ObjectValue(tag,@"accountID"),reminderID=ObjectValue(tag,@"reminderID");
            id accountUUID=ObjectValue(accountID,@"uuid"),reminderUUID=ObjectValue(reminderID,@"uuid");
            if(!identifier || [seen containsObject:identifier] || ![name isKindOfClass:NSString.class] || ![accountUUID isKindOfClass:NSUUID.class] || ![reminderUUID isKindOfClass:NSUUID.class]
                    || ![ObjectValue(accountID,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(reminderID,@"entityName") isEqual:@"REMCDReminder"]
                    || !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([reminderUUID UUIDString],identity[@"id"]) || !MethodMatches([tag class],@"type",@"q@:",NO))return EncodeFlag(FlagFailure(@"snapshot_changed"));
            NSInteger kind=((NSInteger(*)(id,SEL))objc_msgSend)(tag,NSSelectorFromString(@"type"));
            if(![expected containsObject:@{@"id":identifier,@"name":name,@"native_type":@(kind)}])return EncodeFlag(FlagFailure(@"snapshot_changed"));
            [seen addObject:identifier];if([name caseInsensitiveCompare:tagName]==NSOrderedSame)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        }
        id added=((id(*)(id,SEL,NSInteger,id))objc_msgSend)(context,NSSelectorFromString(@"addHashtagWithType:name:"),0,tagName);
        NSString *addedID=ObjectUUID(added,@"REMCDHashtag");id accountID=ObjectValue(added,@"accountID"),reminderID=ObjectValue(added,@"reminderID");
        id accountUUID=ObjectValue(accountID,@"uuid"),reminderUUID=ObjectValue(reminderID,@"uuid");
        if(!addedID || [seen containsObject:addedID] || ![ObjectValue(added,@"name") isEqual:tagName] || !MethodMatches([added class],@"type",@"q@:",NO)
                || ((NSInteger(*)(id,SEL))objc_msgSend)(added,NSSelectorFromString(@"type"))!=0
                || ![accountUUID isKindOfClass:NSUUID.class] || ![reminderUUID isKindOfClass:NSUUID.class]
                || ![ObjectValue(accountID,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(reminderID,@"entityName") isEqual:@"REMCDReminder"]
                || !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([reminderUUID UUIDString],identity[@"id"]))return EncodeFlag(FlagFailure(@"snapshot_changed"));
        NSArray *staged=array(ObjectValue(context,@"hashtags"));if(!staged || staged.count!=expected.count+1)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        NSMutableSet *remaining=[NSMutableSet set];for(id tag in staged){
            NSString *identifier=ObjectUUID(tag,@"REMCDHashtag");id name=ObjectValue(tag,@"name"),account=ObjectValue(tag,@"accountID"),task=ObjectValue(tag,@"reminderID");
            id au=ObjectValue(account,@"uuid"),tu=ObjectValue(task,@"uuid");
            if(!identifier || [remaining containsObject:identifier] || ![name isKindOfClass:NSString.class] || ![au isKindOfClass:NSUUID.class] || ![tu isKindOfClass:NSUUID.class]
                    || ![ObjectValue(account,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(task,@"entityName") isEqual:@"REMCDReminder"]
                    || !UUIDEqual([au UUIDString],identity[@"source_id"]) || !UUIDEqual([tu UUIDString],identity[@"id"]) || !MethodMatches([tag class],@"type",@"q@:",NO))return EncodeFlag(FlagFailure(@"snapshot_changed"));
            NSInteger kind=((NSInteger(*)(id,SEL))objc_msgSend)(tag,NSSelectorFromString(@"type"));
            if(!UUIDEqual(identifier,addedID) && ![expected containsObject:@{@"id":identifier,@"name":name,@"native_type":@(kind)}])return EncodeFlag(FlagFailure(@"snapshot_changed"));
            [remaining addObject:identifier];
        }
        [remaining removeObject:addedID];if(![seen isEqual:remaining])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        if(!enterMutation())return EncodeFlag(FlagFailure(@"native_dispatch_cancelled"));
        dispatched=YES;NSError *error=nil;BOOL saved=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(save,NSSelectorFromString(@"saveSynchronouslyWithError:"),&error);
        if(!saved || error)return EncodeFlag(@{@"protocol_version":@1,@"error":@"mutation_failed_or_uncertain",@"mutation_dispatched":@YES});
        return EncodeFlag(@{@"protocol_version":@1,@"result":@{@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"saved_locally":@YES,@"tag_id":addedID}});
    }@catch(NSException *exception){return EncodeFlag(@{@"protocol_version":@1,@"error":dispatched?@"mutation_failed_or_uncertain":@"private_metadata_unavailable",@"mutation_dispatched":@(dispatched)});}
}

// Replace one tag relationship in a single guarded native save.
NSData *ICloudRemindersReplaceTag(NSData *data,BOOL (^enterMutation)(void)){
    __block BOOL dispatched=NO;
    @try{
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=6 || !enterMutation)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSString *tagName=request[@"tag_name"],*removedID=request[@"tag_id"];NSArray *expected=request[@"expected_tags"];
        if(![removedID isKindOfClass:NSString.class] || ![[NSUUID alloc]initWithUUIDString:removedID] || ![tagName isKindOfClass:NSString.class] || tagName.length<1 || tagName.length>200 || [tagName lengthOfBytesUsingEncoding:NSUTF8StringEncoding]>512 || ![expected isKindOfClass:NSArray.class] || expected.count<1 || expected.count>200)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSMutableCharacterSet *allowed=[[NSCharacterSet alphanumericCharacterSet] mutableCopy];[allowed formUnionWithCharacterSet:[NSCharacterSet nonBaseCharacterSet]];[allowed addCharactersInString:@"_"];
        if([tagName rangeOfCharacterFromSet:allowed.invertedSet].location!=NSNotFound || [[NSCharacterSet nonBaseCharacterSet] characterIsMember:[tagName characterAtIndex:0]])return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSDictionary *identity=@{@"id":request[@"id"] ?: [NSNull null],@"source_id":request[@"source_id"] ?: [NSNull null],@"list_id":request[@"list_id"] ?: [NSNull null]};
        NSData *identityData=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];NSDictionary *failure=nil;id store=nil;
        id reminder=SelectedReminderWithStore(identityData,&failure,&store);if(!reminder || !store)return EncodeFlag(failure ?: FlagFailure(@"private_store_unavailable"));
        id observed=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadTags(identityData) options:0 error:NULL];
        if(![observed[@"native_metadata"][@"tags"] isEqual:expected])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        Class cls=NSClassFromString(@"REMSaveRequest");
        if(!MethodMatches(cls,@"initWithStore:",@"@@:@",NO) || !MethodMatches(cls,@"updateReminder:",@"@@:@",NO) || !MethodMatches(cls,@"saveSynchronouslyWithError:",@"B@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_tag_write"));
        id save=((id(*)(id,SEL,id))objc_msgSend)([cls alloc],NSSelectorFromString(@"initWithStore:"),store);
        id change=((id(*)(id,SEL,id))objc_msgSend)(save,NSSelectorFromString(@"updateReminder:"),reminder),context=ObjectValue(change,@"hashtagContext"),collection=ObjectValue(context,@"hashtags");
        if(!context || (!MethodMatches([context class],@"addHashtagWithType:name:",@"@@:q@",NO) || !MethodMatches([context class],@"removeHashtag:",@"v@:@",NO)))return EncodeFlag(FlagFailure(@"unsupported_tag_write"));
        NSArray *(^array)(id)=^NSArray *(id value){if([value isKindOfClass:NSArray.class])return value;if([value isKindOfClass:NSSet.class])return [value allObjects];if([value isKindOfClass:NSOrderedSet.class])return [value array];return nil;};
        NSArray *values=array(collection);if(!values || values.count!=expected.count)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        NSMutableSet *seen=[NSMutableSet set];id target=nil;
        for(id tag in values){
            NSString *identifier=ObjectUUID(tag,@"REMCDHashtag");id name=ObjectValue(tag,@"name"),accountID=ObjectValue(tag,@"accountID"),reminderID=ObjectValue(tag,@"reminderID");
            id accountUUID=ObjectValue(accountID,@"uuid"),reminderUUID=ObjectValue(reminderID,@"uuid");
            if(!identifier || [seen containsObject:identifier] || ![name isKindOfClass:NSString.class] || ![accountUUID isKindOfClass:NSUUID.class] || ![reminderUUID isKindOfClass:NSUUID.class]
                    || ![ObjectValue(accountID,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(reminderID,@"entityName") isEqual:@"REMCDReminder"]
                    || !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([reminderUUID UUIDString],identity[@"id"]) || !MethodMatches([tag class],@"type",@"q@:",NO))return EncodeFlag(FlagFailure(@"snapshot_changed"));
            NSInteger kind=((NSInteger(*)(id,SEL))objc_msgSend)(tag,NSSelectorFromString(@"type"));
            if(![expected containsObject:@{@"id":identifier,@"name":name,@"native_type":@(kind)}])return EncodeFlag(FlagFailure(@"snapshot_changed"));
            [seen addObject:identifier];if(UUIDEqual(identifier,removedID))target=tag;if([name caseInsensitiveCompare:tagName]==NSOrderedSame)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        }
        if(!target)return EncodeFlag(FlagFailure(@"not_found_in_scope"));
        NSString *removedCanonical=ObjectUUID(target,@"REMCDHashtag");
        ((void(*)(id,SEL,id))objc_msgSend)(context,NSSelectorFromString(@"removeHashtag:"),target);
        NSArray *afterRemoval=array(ObjectValue(context,@"hashtags"));if(!afterRemoval || afterRemoval.count+1!=expected.count)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        id added=((id(*)(id,SEL,NSInteger,id))objc_msgSend)(context,NSSelectorFromString(@"addHashtagWithType:name:"),0,tagName);
        NSString *addedID=ObjectUUID(added,@"REMCDHashtag");id accountID=ObjectValue(added,@"accountID"),reminderID=ObjectValue(added,@"reminderID");
        id accountUUID=ObjectValue(accountID,@"uuid"),reminderUUID=ObjectValue(reminderID,@"uuid");
        if(!addedID || [seen containsObject:addedID] || ![ObjectValue(added,@"name") isEqual:tagName] || !MethodMatches([added class],@"type",@"q@:",NO)
                || ((NSInteger(*)(id,SEL))objc_msgSend)(added,NSSelectorFromString(@"type"))!=0
                || ![accountUUID isKindOfClass:NSUUID.class] || ![reminderUUID isKindOfClass:NSUUID.class]
                || ![ObjectValue(accountID,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(reminderID,@"entityName") isEqual:@"REMCDReminder"]
                || !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([reminderUUID UUIDString],identity[@"id"]))return EncodeFlag(FlagFailure(@"snapshot_changed"));
        NSArray *staged=array(ObjectValue(context,@"hashtags"));if(!staged || staged.count!=expected.count)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        NSMutableSet *remaining=[NSMutableSet set];for(id tag in staged){
            NSString *identifier=ObjectUUID(tag,@"REMCDHashtag");id name=ObjectValue(tag,@"name"),account=ObjectValue(tag,@"accountID"),task=ObjectValue(tag,@"reminderID");
            id au=ObjectValue(account,@"uuid"),tu=ObjectValue(task,@"uuid");
            if(!identifier || [remaining containsObject:identifier] || ![name isKindOfClass:NSString.class] || ![au isKindOfClass:NSUUID.class] || ![tu isKindOfClass:NSUUID.class]
                    || ![ObjectValue(account,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(task,@"entityName") isEqual:@"REMCDReminder"]
                    || !UUIDEqual([au UUIDString],identity[@"source_id"]) || !UUIDEqual([tu UUIDString],identity[@"id"]) || !MethodMatches([tag class],@"type",@"q@:",NO))return EncodeFlag(FlagFailure(@"snapshot_changed"));
            NSInteger kind=((NSInteger(*)(id,SEL))objc_msgSend)(tag,NSSelectorFromString(@"type"));
            if(!UUIDEqual(identifier,addedID) && ![expected containsObject:@{@"id":identifier,@"name":name,@"native_type":@(kind)}])return EncodeFlag(FlagFailure(@"snapshot_changed"));
            [remaining addObject:identifier];
        }
        [remaining removeObject:addedID];[seen removeObject:removedCanonical];if(![seen isEqual:remaining])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        if(!enterMutation())return EncodeFlag(FlagFailure(@"native_dispatch_cancelled"));
        dispatched=YES;NSError *error=nil;BOOL saved=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(save,NSSelectorFromString(@"saveSynchronouslyWithError:"),&error);
        if(!saved || error)return EncodeFlag(@{@"protocol_version":@1,@"error":@"mutation_failed_or_uncertain",@"mutation_dispatched":@YES});
        return EncodeFlag(@{@"protocol_version":@1,@"result":@{@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"saved_locally":@YES,@"tag_id":addedID}});
    }@catch(NSException *exception){return EncodeFlag(@{@"protocol_version":@1,@"error":dispatched?@"mutation_failed_or_uncertain":@"private_metadata_unavailable",@"mutation_dispatched":@(dispatched)});}
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

// Inspect one exact root in the provider recovery view. Never return other items.
NSData *ICloudRemindersRecoveryLookup(NSData *data){
    @try{
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=2)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSString *source=request[@"source_id"],*identifier=request[@"id"];
        if(![source isKindOfClass:NSString.class] || ![identifier isKindOfClass:NSString.class] ||
           ![[NSUUID alloc]initWithUUIDString:source] || ![[NSUUID alloc]initWithUUIDString:identifier])return EncodeFlag(FlagFailure(@"unsupported_public_identity"));
        if(!FlagReadInterface() || !dlopen("/System/Library/PrivateFrameworks/ReminderKitInternal.framework/ReminderKitInternal",RTLD_LAZY|RTLD_LOCAL))return EncodeFlag(FlagFailure(@"unsupported_interface"));
        Class storeClass=NSClassFromString(@"REMStore"),invocationClass=NSClassFromString(@"REMRemindersListDataView_RecentlyDeletedInvocation");
        if(!MethodMatches(storeClass,@"fetchAccountsWithError:",@"@@:^@",NO) ||
           !MethodMatches(storeClass,@"resultFromPerformingSwiftInvocation:parametersData:storages:error:",@"@@:@@@^@",NO) ||
           !MethodMatches(invocationClass,@"initWithFetchResultTokenToDiffAgainst:",@"@@:@",NO))return EncodeFlag(FlagFailure(@"unsupported_recovery_interface"));
        id store=[storeClass new];NSError *error=nil;
        id accounts=((id(*)(id,SEL,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchAccountsWithError:"),&error);
        if(error || ![accounts isKindOfClass:NSArray.class] || [accounts count]>100)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        id selected=nil;
        for(id account in accounts)if(UUIDEqual(ObjectUUID(account,@"REMCDAccount"),source)){if(selected)return EncodeFlag(FlagFailure(@"native_identity_mismatch"));selected=account;}
        if(!selected)return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
        id capabilities=ObjectValue(selected,@"capabilities");
        if(!MethodMatches([capabilities class],@"supportsRecentlyDeletedList",@"B@:",NO) || !((BOOL(*)(id,SEL))objc_msgSend)(capabilities,NSSelectorFromString(@"supportsRecentlyDeletedList")))return EncodeFlag(FlagFailure(@"unsupported_recovery_interface"));
        NSDictionary *parameters=@{@"accountID":@{@"uuid":source,@"entityName":@"REMCDAccount"},@"deletedAfterDate":[NSDate dateWithTimeIntervalSinceNow:-30*24*60*60],@"remindersPrefetch":@{@"base":@"none"},@"countCompleted":@YES};
        NSData *payload=[NSPropertyListSerialization dataWithPropertyList:parameters format:NSPropertyListBinaryFormat_v1_0 options:0 error:&error];
        id invocation=((id(*)(id,SEL,id))objc_msgSend)([invocationClass alloc],NSSelectorFromString(@"initWithFetchResultTokenToDiffAgainst:"),nil);
        if(error || !payload || !invocation)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        id response=((id(*)(id,SEL,id,id,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"resultFromPerformingSwiftInvocation:parametersData:storages:error:"),invocation,payload,nil,&error);
        NSData *bytes=ObjectValue(response,@"resultData");
        if(error || ![bytes isKindOfClass:NSData.class] || bytes.length>4000000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        id decoded=[NSPropertyListSerialization propertyListWithData:bytes options:NSPropertyListImmutable format:NULL error:&error];
        id model=[decoded isKindOfClass:NSDictionary.class]?decoded[@"model"]:nil;
        id values=[model isKindOfClass:NSDictionary.class]?model[@"reminders"]:nil;
        if(error || ![values isKindOfClass:NSArray.class] || [values count]>5000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        // Traverse only provider-returned nodes; reject cycles, duplicates and overlarge trees.
        NSMutableArray *queue=[NSMutableArray array];
        for(id value in values)[queue addObject:@{@"node":value,@"parent":[NSNull null],@"root":[NSNull null],@"depth":@0}];
        NSMutableSet *visited=[NSMutableSet set];id selectedRoot=nil;id selectedParent=[NSNull null],selectedRecoveryRoot=[NSNull null];
        NSUInteger matches=0,index=0;
        while(index<queue.count){
            if(queue.count>5000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
            NSDictionary *entry=queue[index++];id value=entry[@"node"];
            id objectID=[value isKindOfClass:NSDictionary.class]?value[@"objectID"]:nil;
            NSString *uuid=[objectID isKindOfClass:NSDictionary.class]?objectID[@"uuid"]:nil;
            id entity=[objectID isKindOfClass:NSDictionary.class]?objectID[@"entityName"]:nil;
            NSUUID *parsed=[uuid isKindOfClass:NSString.class]?[[NSUUID alloc]initWithUUIDString:uuid]:nil;
            NSString *canonical=parsed.UUIDString;
            if(!canonical || ![entity isEqual:@"REMCDReminder"] || [visited containsObject:canonical])return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
            [visited addObject:canonical];
            id root=entry[@"root"]==[NSNull null]?canonical:entry[@"root"];
            if(UUIDEqual(canonical,identifier)){matches++;selectedRoot=value;selectedParent=entry[@"parent"];selectedRecoveryRoot=selectedParent==[NSNull null]?identifier:root;}
            id descendants=value[@"subtasks"];
            if(descendants){
                if(![descendants isKindOfClass:NSArray.class] || [descendants count]>100 || ([descendants count] && [entry[@"depth"] unsignedIntegerValue]>=10))return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
                for(id child in descendants)[queue addObject:@{@"node":child,@"parent":canonical,@"root":root,@"depth":@([entry[@"depth"] unsignedIntegerValue]+1)}];
            }
        }
        if(matches>1)return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
        id children=[NSNull null];NSString *childrenStatus=@"unavailable";
        if(selectedRoot && selectedRoot[@"subtasks"]){
            id values=selectedRoot[@"subtasks"];
            if(![values isKindOfClass:NSArray.class] || [values count]>100)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
            NSMutableArray *ids=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];
            for(id value in values){
                id objectID=[value isKindOfClass:NSDictionary.class]?value[@"objectID"]:nil;
                NSString *uuid=[objectID isKindOfClass:NSDictionary.class]?objectID[@"uuid"]:nil;
                id entity=[objectID isKindOfClass:NSDictionary.class]?objectID[@"entityName"]:nil;
                NSUUID *parsed=[uuid isKindOfClass:NSString.class]?[[NSUUID alloc]initWithUUIDString:uuid]:nil;
                NSString *canonical=parsed.UUIDString;
                if(!canonical || ![entity isEqual:@"REMCDReminder"] || UUIDEqual(canonical,identifier) || [seen containsObject:canonical])return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
                [seen addObject:canonical];[ids addObject:canonical];
            }
            [ids sortUsingSelector:@selector(compare:)];children=ids;childrenStatus=@"provider_reported";
        }
        return EncodeFlag(@{@"protocol_version":@1,@"source_id":source,@"id":identifier,@"found_in_view":@((BOOL)(matches==1)),@"found_as_root":@((BOOL)(matches==1 && selectedParent==[NSNull null])),@"parent_id":selectedParent,@"recovery_root_id":selectedRecoveryRoot,@"view":@"provider_recently_deleted",@"reported_child_ids":children,@"children_status":childrenStatus,@"children_complete":@NO,@"observation_consistency":@"separate_provider_reads",@"data_mutated":@NO});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}

// One scoped, bounded recovery-view snapshot; never claims complete hierarchy.
NSData *ICloudRemindersRecoveryTree(NSData *data){
    @try{
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=2)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSString *source=request[@"source_id"],*identifier=request[@"id"];
        if(![source isKindOfClass:NSString.class] || ![identifier isKindOfClass:NSString.class] ||
           ![[NSUUID alloc]initWithUUIDString:source] || ![[NSUUID alloc]initWithUUIDString:identifier])return EncodeFlag(FlagFailure(@"unsupported_public_identity"));
        if(!FlagReadInterface() || !dlopen("/System/Library/PrivateFrameworks/ReminderKitInternal.framework/ReminderKitInternal",RTLD_LAZY|RTLD_LOCAL))return EncodeFlag(FlagFailure(@"unsupported_interface"));
        Class storeClass=NSClassFromString(@"REMStore"),invocationClass=NSClassFromString(@"REMRemindersListDataView_RecentlyDeletedInvocation");
        if(!MethodMatches(storeClass,@"fetchAccountsWithError:",@"@@:^@",NO) ||
           !MethodMatches(storeClass,@"resultFromPerformingSwiftInvocation:parametersData:storages:error:",@"@@:@@@^@",NO) ||
           !MethodMatches(invocationClass,@"initWithFetchResultTokenToDiffAgainst:",@"@@:@",NO))return EncodeFlag(FlagFailure(@"unsupported_recovery_interface"));
        id store=[storeClass new];NSError *error=nil;
        id accounts=((id(*)(id,SEL,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchAccountsWithError:"),&error);
        if(error || ![accounts isKindOfClass:NSArray.class] || [accounts count]>100)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        id selected=nil;
        for(id account in accounts)if(UUIDEqual(ObjectUUID(account,@"REMCDAccount"),source)){if(selected)return EncodeFlag(FlagFailure(@"native_identity_mismatch"));selected=account;}
        if(!selected)return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
        id capabilities=ObjectValue(selected,@"capabilities");
        if(!MethodMatches([capabilities class],@"supportsRecentlyDeletedList",@"B@:",NO) || !((BOOL(*)(id,SEL))objc_msgSend)(capabilities,NSSelectorFromString(@"supportsRecentlyDeletedList")))return EncodeFlag(FlagFailure(@"unsupported_recovery_interface"));
        NSDictionary *parameters=@{@"accountID":@{@"uuid":source,@"entityName":@"REMCDAccount"},@"deletedAfterDate":[NSDate dateWithTimeIntervalSinceNow:-30*24*60*60],@"remindersPrefetch":@{@"base":@"none"},@"countCompleted":@YES};
        NSData *payload=[NSPropertyListSerialization dataWithPropertyList:parameters format:NSPropertyListBinaryFormat_v1_0 options:0 error:&error];
        id invocation=((id(*)(id,SEL,id))objc_msgSend)([invocationClass alloc],NSSelectorFromString(@"initWithFetchResultTokenToDiffAgainst:"),nil);
        if(error || !payload || !invocation)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        id response=((id(*)(id,SEL,id,id,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"resultFromPerformingSwiftInvocation:parametersData:storages:error:"),invocation,payload,nil,&error);
        NSData *bytes=ObjectValue(response,@"resultData");
        if(error || ![bytes isKindOfClass:NSData.class] || bytes.length>4000000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        id decoded=[NSPropertyListSerialization propertyListWithData:bytes options:NSPropertyListImmutable format:NULL error:&error];
        id model=[decoded isKindOfClass:NSDictionary.class]?decoded[@"model"]:nil;
        id values=[model isKindOfClass:NSDictionary.class]?model[@"reminders"]:nil;
        if(error || ![values isKindOfClass:NSArray.class] || [values count]>5000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        // Traverse only provider-returned nodes; reject cycles, duplicates and overlarge trees.
        NSMutableArray *queue=[NSMutableArray array];
        for(id value in values)[queue addObject:@{@"node":value,@"parent":[NSNull null],@"root":[NSNull null],@"depth":@0}];
        NSMutableSet *visited=[NSMutableSet set];id selectedRoot=nil;id selectedParent=[NSNull null],selectedRecoveryRoot=[NSNull null];
        NSUInteger matches=0,index=0;
        while(index<queue.count){
            if(queue.count>5000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
            NSDictionary *entry=queue[index++];id value=entry[@"node"];
            id objectID=[value isKindOfClass:NSDictionary.class]?value[@"objectID"]:nil;
            NSString *uuid=[objectID isKindOfClass:NSDictionary.class]?objectID[@"uuid"]:nil;
            id entity=[objectID isKindOfClass:NSDictionary.class]?objectID[@"entityName"]:nil;
            NSUUID *parsed=[uuid isKindOfClass:NSString.class]?[[NSUUID alloc]initWithUUIDString:uuid]:nil;
            NSString *canonical=parsed.UUIDString;
            if(!canonical || ![entity isEqual:@"REMCDReminder"] || [visited containsObject:canonical])return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
            [visited addObject:canonical];
            id root=entry[@"root"]==[NSNull null]?canonical:entry[@"root"];
            if(UUIDEqual(canonical,identifier)){matches++;selectedRoot=value;selectedParent=entry[@"parent"];selectedRecoveryRoot=selectedParent==[NSNull null]?identifier:root;}
            id descendants=value[@"subtasks"];
            if(descendants){
                if(![descendants isKindOfClass:NSArray.class] || [descendants count]>100 || ([descendants count] && [entry[@"depth"] unsignedIntegerValue]>=10))return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
                for(id child in descendants)[queue addObject:@{@"node":child,@"parent":canonical,@"root":root,@"depth":@([entry[@"depth"] unsignedIntegerValue]+1)}];
            }
        }
        if(matches>1)return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
        if(matches!=1 || ![selectedRecoveryRoot isKindOfClass:NSString.class])return EncodeFlag(FlagFailure(@"not_found_in_scope"));
        NSMutableArray *nodes=[NSMutableArray array];
        Class objectClass=NSClassFromString(@"REMObjectID");
        if(!MethodMatches([store class],@"fetchReminderIncludingMarkedForDeleteWithObjectID:error:",@"@@:@^@",NO))return EncodeFlag(FlagFailure(@"unsupported_recovery_interface"));
        for(NSDictionary *entry in queue){
            id value=entry[@"node"],objectID=value[@"objectID"],uuid=objectID[@"uuid"];
            NSString *canonical=[[[NSUUID alloc]initWithUUIDString:uuid] UUIDString];
            id root=entry[@"root"]==[NSNull null]?canonical:entry[@"root"];
            if(!UUIDEqual(root,selectedRecoveryRoot))continue;
            if(nodes.count>=100)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
            id raw=value[@"subtasks"],children=[NSNull null];NSString *status=@"unavailable";
            if(raw){NSMutableArray *ids=[NSMutableArray array];for(id child in raw){id obj=child[@"objectID"];NSString *childID=[[[NSUUID alloc]initWithUUIDString:obj[@"uuid"]] UUIDString];if(!childID)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));[ids addObject:childID];}[ids sortUsingSelector:@selector(compare:)];children=ids;status=@"provider_reported";}
            NSURL *url=[NSURL URLWithString:[@"x-apple-reminderkit://REMCDReminder/" stringByAppendingString:canonical]];
            id nativeObjectID=((id(*)(id,SEL,id))objc_msgSend)((id)objectClass,NSSelectorFromString(@"objectIDWithURL:"),url);error=nil;
            id reminder=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchReminderIncludingMarkedForDeleteWithObjectID:error:"),nativeObjectID,&error);
            NSString *title=ObjectValue(reminder,@"titleAsString");id account=ObjectValue(reminder,@"account");
            if(error || !UUIDEqual(ObjectUUID(reminder,@"REMCDReminder"),canonical) || !UUIDEqual(ObjectUUID(account,@"REMCDAccount"),source) || ![title isKindOfClass:NSString.class] || title.length>1000 || [title lengthOfBytesUsingEncoding:NSUTF8StringEncoding]>4000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
            [nodes addObject:@{@"id":canonical,@"title":title,@"parent_id":entry[@"parent"],@"depth":entry[@"depth"],@"reported_child_ids":children,@"children_status":status}];
        }
        if(!nodes.count)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        return EncodeFlag(@{@"protocol_version":@1,@"source_id":source,@"requested_id":identifier,@"root_id":selectedRecoveryRoot,@"nodes":nodes,@"view":@"provider_recently_deleted",@"hierarchy_complete":@NO,@"observation_consistency":@"single_provider_view",@"title_observation_consistency":@"separate_object_reads",@"data_mutated":@NO});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}

// Bounded recovery-root discovery; title-only projection from selected account.
NSData *ICloudRemindersRecoveryList(NSData *data){
    @try{
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=3)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSString *source=request[@"source_id"];id limit=request[@"limit"],offset=request[@"offset"];
        if(![source isKindOfClass:NSString.class] || ![[NSUUID alloc]initWithUUIDString:source] || ![limit isKindOfClass:NSNumber.class] || ![offset isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)limit)==CFBooleanGetTypeID() || CFGetTypeID((__bridge CFTypeRef)offset)==CFBooleanGetTypeID() || [limit integerValue]<1 || [limit integerValue]>20 || [offset integerValue]<0 || [offset integerValue]>5000 || [limit doubleValue]!=(double)[limit integerValue] || [offset doubleValue]!=(double)[offset integerValue])return EncodeFlag(FlagFailure(@"unsupported_public_identity"));
        if(!FlagReadInterface() || !dlopen("/System/Library/PrivateFrameworks/ReminderKitInternal.framework/ReminderKitInternal",RTLD_LAZY|RTLD_LOCAL))return EncodeFlag(FlagFailure(@"unsupported_interface"));
        Class storeClass=NSClassFromString(@"REMStore"),invocationClass=NSClassFromString(@"REMRemindersListDataView_RecentlyDeletedInvocation");
        if(!MethodMatches(storeClass,@"fetchAccountsWithError:",@"@@:^@",NO) ||
           !MethodMatches(storeClass,@"resultFromPerformingSwiftInvocation:parametersData:storages:error:",@"@@:@@@^@",NO) ||
           !MethodMatches(invocationClass,@"initWithFetchResultTokenToDiffAgainst:",@"@@:@",NO))return EncodeFlag(FlagFailure(@"unsupported_recovery_interface"));
        id store=[storeClass new];NSError *error=nil;
        id accounts=((id(*)(id,SEL,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchAccountsWithError:"),&error);
        if(error || ![accounts isKindOfClass:NSArray.class] || [accounts count]>100)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        id selected=nil;
        for(id account in accounts)if(UUIDEqual(ObjectUUID(account,@"REMCDAccount"),source)){if(selected)return EncodeFlag(FlagFailure(@"native_identity_mismatch"));selected=account;}
        if(!selected)return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
        id capabilities=ObjectValue(selected,@"capabilities");
        if(!MethodMatches([capabilities class],@"supportsRecentlyDeletedList",@"B@:",NO) || !((BOOL(*)(id,SEL))objc_msgSend)(capabilities,NSSelectorFromString(@"supportsRecentlyDeletedList")))return EncodeFlag(FlagFailure(@"unsupported_recovery_interface"));
        NSDictionary *parameters=@{@"accountID":@{@"uuid":source,@"entityName":@"REMCDAccount"},@"deletedAfterDate":[NSDate dateWithTimeIntervalSinceNow:-30*24*60*60],@"remindersPrefetch":@{@"base":@"none"},@"countCompleted":@YES};
        NSData *payload=[NSPropertyListSerialization dataWithPropertyList:parameters format:NSPropertyListBinaryFormat_v1_0 options:0 error:&error];
        id invocation=((id(*)(id,SEL,id))objc_msgSend)([invocationClass alloc],NSSelectorFromString(@"initWithFetchResultTokenToDiffAgainst:"),nil);
        if(error || !payload || !invocation)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        id response=((id(*)(id,SEL,id,id,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"resultFromPerformingSwiftInvocation:parametersData:storages:error:"),invocation,payload,nil,&error);
        NSData *bytes=ObjectValue(response,@"resultData");
        if(error || ![bytes isKindOfClass:NSData.class] || bytes.length>4000000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        id decoded=[NSPropertyListSerialization propertyListWithData:bytes options:NSPropertyListImmutable format:NULL error:&error];
        id model=[decoded isKindOfClass:NSDictionary.class]?decoded[@"model"]:nil;
        id values=[model isKindOfClass:NSDictionary.class]?model[@"reminders"]:nil;
        if(error || ![values isKindOfClass:NSArray.class] || [values count]>5000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
        Class objectClass=NSClassFromString(@"REMObjectID");
        if(!MethodMatches([store class],@"fetchReminderIncludingMarkedForDeleteWithObjectID:error:",@"@@:@^@",NO))return EncodeFlag(FlagFailure(@"unsupported_recovery_interface"));
        NSMutableArray *identities=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];
        for(id value in values){id obj=[value isKindOfClass:NSDictionary.class]?value[@"objectID"]:nil;NSString *uuid=[obj isKindOfClass:NSDictionary.class]?obj[@"uuid"]:nil;
            NSUUID *parsed=[uuid isKindOfClass:NSString.class]?[[NSUUID alloc]initWithUUIDString:uuid]:nil;NSString *canonical=parsed.UUIDString;
            if(!canonical || ![obj[@"entityName"] isEqual:@"REMCDReminder"] || [seen containsObject:canonical])return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
            [seen addObject:canonical];[identities addObject:canonical];}
        NSUInteger start=MIN((NSUInteger)[offset integerValue],identities.count),end=MIN(start+(NSUInteger)[limit integerValue],identities.count);NSMutableArray *items=[NSMutableArray array];
        for(NSUInteger index=start;index<end;index++){
            NSString *identifier=identities[index];NSURL *url=[NSURL URLWithString:[@"x-apple-reminderkit://REMCDReminder/" stringByAppendingString:identifier]];
            id objectID=((id(*)(id,SEL,id))objc_msgSend)((id)objectClass,NSSelectorFromString(@"objectIDWithURL:"),url);error=nil;
            id reminder=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchReminderIncludingMarkedForDeleteWithObjectID:error:"),objectID,&error);
            NSString *title=ObjectValue(reminder,@"titleAsString");id account=ObjectValue(reminder,@"account");
            if(error || !UUIDEqual(ObjectUUID(reminder,@"REMCDReminder"),identifier) || !UUIDEqual(ObjectUUID(account,@"REMCDAccount"),source) || ![title isKindOfClass:NSString.class] || title.length>1000 || [title lengthOfBytesUsingEncoding:NSUTF8StringEncoding]>4000)return EncodeFlag(FlagFailure(@"recovery_read_unavailable"));
            [items addObject:@{@"id":identifier,@"source_id":source,@"title":title}];
        }
        return EncodeFlag(@{@"protocol_version":@1,@"source_id":source,@"items":items,@"done":@((BOOL)(end==identities.count)),@"next_offset":@([offset integerValue]+items.count),@"view":@"provider_recently_deleted_roots",@"pagination_consistency":@"live_offset_may_shift",@"data_mutated":@NO});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}
