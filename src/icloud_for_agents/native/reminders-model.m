// Optional fixed ReminderKit reads and gated single-flag/tag saves. No SQL, body projection or selectors from JSON.
#import "reminders-model.h"
#import <objc/runtime.h>
#import <objc/message.h>
#import <dlfcn.h>
#import <CommonCrypto/CommonDigest.h>
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

// Read ordinary native sections only after EventKit has selected the exact list.
NSData *ICloudRemindersListSections(NSData *data){
    @try {
        id identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![identity isKindOfClass:NSDictionary.class] || [identity count]!=2)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        for(NSString *key in @[@"source_id",@"list_id"]){id value=identity[key];NSUUID *uuid=[value isKindOfClass:NSString.class]?[[NSUUID alloc]initWithUUIDString:value]:nil;if(!uuid || !UUIDEqual(uuid.UUIDString,value))return EncodeFlag(FlagFailure(@"unsupported_public_identity"));}
        if(NSProcessInfo.processInfo.operatingSystemVersion.majorVersion!=27 || !dlopen("/System/Library/PrivateFrameworks/ReminderKit.framework/ReminderKit",RTLD_LAZY|RTLD_LOCAL))return EncodeFlag(FlagFailure(@"unsupported_interface"));
        Class ids=NSClassFromString(@"REMObjectID"),storeClass=NSClassFromString(@"REMStore");
        if(!MethodMatches(ids,@"objectIDWithURL:",@"@@:@",YES) || !MethodMatches(storeClass,@"fetchListWithObjectID:error:",@"@@:@^@",NO) || !MethodMatches(storeClass,@"fetchListSectionsWithListObjectID:error:",@"@@:@^@",NO))return EncodeFlag(FlagFailure(@"unsupported_interface"));
        id store=[storeClass new];NSURL *url=[NSURL URLWithString:[@"x-apple-reminderkit://REMCDList/" stringByAppendingString:identity[@"list_id"]]];
        id objectID=((id(*)(id,SEL,id))objc_msgSend)((id)ids,NSSelectorFromString(@"objectIDWithURL:"),url);NSError *error=nil;
        if(!store || !objectID)return EncodeFlag(FlagFailure(@"private_store_unavailable"));
        id list=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchListWithObjectID:error:"),objectID,&error);
        if(error || !list)return EncodeFlag(FlagFailure(@"private_read_denied_or_unavailable"));
        if(!UUIDEqual(ObjectUUID(list,@"REMCDList"),identity[@"list_id"]) || !UUIDEqual(ObjectUUID(ObjectValue(list,@"account"),@"REMCDAccount"),identity[@"source_id"]))return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
        error=nil;id sections=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchListSectionsWithListObjectID:error:"),objectID,&error);
        if(error || ![sections isKindOfClass:NSArray.class] || [sections count]>100)return EncodeFlag(FlagFailure(@"unsupported_section_metadata"));
        NSMutableArray *out=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];
        for(id section in sections){
            NSString *sectionID=ObjectUUID(section,@"REMCDListSection");id storage=ObjectValue(section,@"storage"),name=ObjectValue(storage,@"displayName"),account=ObjectValue(storage,@"accountID"),parent=ObjectValue(storage,@"listID");
            id accountUUID=ObjectValue(account,@"uuid"),listUUID=ObjectValue(parent,@"uuid");
            if(!sectionID || [seen containsObject:sectionID] || ![name isKindOfClass:NSString.class] || [name length]>1000
                    || ![ObjectValue(account,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(parent,@"entityName") isEqual:@"REMCDList"]
                    || ![accountUUID isKindOfClass:NSUUID.class] || ![listUUID isKindOfClass:NSUUID.class]
                    || !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([listUUID UUIDString],identity[@"list_id"]) || !UUIDEqual(ObjectUUID(ObjectValue(section,@"list"),@"REMCDList"),identity[@"list_id"]))return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
            [seen addObject:sectionID];[out addObject:@{@"id":sectionID,@"title":name}];
        }
        [out sortUsingComparator:^NSComparisonResult(NSDictionary *a,NSDictionary *b){return [a[@"id"] compare:b[@"id"]];}];
        return EncodeFlag(@{@"protocol_version":@1,@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"sections":out,@"ordering_verified":@NO,@"membership_verified":@NO,@"data_mutated":@NO});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}
// Observe fixed list flags only after EventKit establishes exact list/source scope.
NSData *ICloudRemindersReadListFeatures(NSData *data){
    @try {
        id identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![identity isKindOfClass:NSDictionary.class])return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        id includePinned=identity[@"include_pinned"];BOOL wantsPinned=includePinned!=nil;
        if((wantsPinned && ([identity count]!=3 || CFGetTypeID((__bridge CFTypeRef)includePinned)!=CFBooleanGetTypeID() || ![includePinned boolValue])) || (!wantsPinned && [identity count]!=2))return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        for(NSString *key in @[@"source_id",@"list_id"]){id value=identity[key];NSUUID *uuid=[value isKindOfClass:NSString.class]?[[NSUUID alloc]initWithUUIDString:value]:nil;if(!uuid||!UUIDEqual(uuid.UUIDString,value))return EncodeFlag(FlagFailure(@"unsupported_public_identity"));}
        if(NSProcessInfo.processInfo.operatingSystemVersion.majorVersion!=27 || !dlopen("/System/Library/PrivateFrameworks/ReminderKit.framework/ReminderKit",RTLD_LAZY|RTLD_LOCAL))return EncodeFlag(FlagFailure(@"unsupported_interface"));
        Class ids=NSClassFromString(@"REMObjectID"),storeClass=NSClassFromString(@"REMStore");if(!MethodMatches(ids,@"objectIDWithURL:",@"@@:@",YES)||!MethodMatches(storeClass,@"fetchListWithObjectID:error:",@"@@:@^@",NO))return EncodeFlag(FlagFailure(@"unsupported_interface"));
        id store=[storeClass new];NSURL *url=[NSURL URLWithString:[@"x-apple-reminderkit://REMCDList/" stringByAppendingString:identity[@"list_id"]]];id oid=((id(*)(id,SEL,id))objc_msgSend)((id)ids,NSSelectorFromString(@"objectIDWithURL:"),url);NSError *error=nil;if(!store||!oid)return EncodeFlag(FlagFailure(@"private_store_unavailable"));
        id list=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchListWithObjectID:error:"),oid,&error);if(error||!list)return EncodeFlag(FlagFailure(@"private_read_denied_or_unavailable"));
        if(!UUIDEqual(ObjectUUID(list,@"REMCDList"),identity[@"list_id"])||!UUIDEqual(ObjectUUID(ObjectValue(list,@"account"),@"REMCDAccount"),identity[@"source_id"]))return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
        NSArray *sharingNames=@[@"isShared",@"isSharedToMe",@"canBeShared"];for(NSString *name in sharingNames)if(!MethodMatches([list class],name,@"B@:",NO))return EncodeFlag(FlagFailure(@"unsupported_list_features"));
        BOOL shared=((BOOL(*)(id,SEL))objc_msgSend)(list,NSSelectorFromString(sharingNames[0])),sharedToMe=((BOOL(*)(id,SEL))objc_msgSend)(list,NSSelectorFromString(sharingNames[1])),shareable=((BOOL(*)(id,SEL))objc_msgSend)(list,NSSelectorFromString(sharingNames[2]));
        id storage=ObjectValue(list,@"storage");NSArray *names=@[@"shouldCategorizeGroceryItems",@"shouldSupportGroceryShoppingSession",@"isGroup"];if(!storage)return EncodeFlag(FlagFailure(@"unsupported_list_features"));
        for(NSString *name in names)if(!MethodMatches([storage class],name,@"B@:",NO))return EncodeFlag(FlagFailure(@"unsupported_list_features"));
        BOOL categories=((BOOL(*)(id,SEL))objc_msgSend)(storage,NSSelectorFromString(names[0])),shopping=((BOOL(*)(id,SEL))objc_msgSend)(storage,NSSelectorFromString(names[1])),group=((BOOL(*)(id,SEL))objc_msgSend)(storage,NSSelectorFromString(names[2]));
        NSMutableDictionary *result=[@{@"protocol_version":@1,@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"grocery_categorization":@((BOOL)categories),@"grocery_shopping_session":@((BOOL)shopping),@"is_group":@((BOOL)group),@"shared":@((BOOL)shared),@"shared_to_me":@((BOOL)sharedToMe),@"can_be_shared":@((BOOL)shareable),@"sharing_metadata_complete":@NO,@"list_type_verified":@NO,@"data_mutated":@NO,@"backend":@"reminderkit-list-features-v2"} mutableCopy];
        if(wantsPinned){
            if(!MethodMatches([list class],@"isPinned",@"B@:",NO))return EncodeFlag(FlagFailure(@"unsupported_list_features"));
            BOOL pinned=((BOOL(*)(id,SEL))objc_msgSend)(list,NSSelectorFromString(@"isPinned"));
            result[@"pinned"]=@((BOOL)pinned);result[@"backend"]=@"reminderkit-list-features-v3";
        }
        return EncodeFlag(result);
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}
// Fixed single-section creation with a preassigned ID and exact prior inventory.
NSData *ICloudRemindersCreateSection(NSData *data,BOOL (^enterMutation)(void)){
    __block BOOL dispatched=NO;
    @try {
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=5 || !enterMutation || ![request[@"title"] isKindOfClass:NSString.class] || ![request[@"title"] length] || [request[@"title"] length]>1000 || ![request[@"expected_sections"] isKindOfClass:NSArray.class])return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSString *newID=request[@"section_id"];NSUUID *uuid=[newID isKindOfClass:NSString.class]?[[NSUUID alloc]initWithUUIDString:newID]:nil;
        if(!uuid || !UUIDEqual(uuid.UUIDString,newID))return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSDictionary *identity=@{@"source_id":request[@"source_id"]?:[NSNull null],@"list_id":request[@"list_id"]?:[NSNull null]};NSData *identityData=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];
        id observed=[NSJSONSerialization JSONObjectWithData:ICloudRemindersListSections(identityData) options:0 error:NULL];
        if(![observed[@"sections"] isEqual:request[@"expected_sections"]])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        Class ids=NSClassFromString(@"REMObjectID"),storeClass=NSClassFromString(@"REMStore"),saveClass=NSClassFromString(@"REMSaveRequest");
        if(!MethodMatches(storeClass,@"fetchListSectionWithObjectID:error:",@"@@:@^@",NO) || !MethodMatches(saveClass,@"initWithStore:",@"@@:@",NO) || !MethodMatches(saveClass,@"updateList:",@"@@:@",NO) || !MethodMatches(saveClass,@"addListSectionWithDisplayName:toListSectionContextChangeItem:listSectionObjectID:",@"@@:@@@",NO) || !MethodMatches(saveClass,@"saveSynchronouslyWithError:",@"B@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_section_write"));
        id store=[storeClass new];SEL make=NSSelectorFromString(@"objectIDWithURL:");NSURL *listURL=[NSURL URLWithString:[@"x-apple-reminderkit://REMCDList/" stringByAppendingString:identity[@"list_id"]]],*sectionURL=[NSURL URLWithString:[@"x-apple-reminderkit://REMCDListSection/" stringByAppendingString:newID]];
        id listID=((id(*)(id,SEL,id))objc_msgSend)((id)ids,make,listURL),sectionID=((id(*)(id,SEL,id))objc_msgSend)((id)ids,make,sectionURL);if(!store||!listID||!sectionID)return EncodeFlag(FlagFailure(@"private_store_unavailable"));
        NSError *error=nil;id existing=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchListSectionWithObjectID:error:"),sectionID,&error);
        if(existing || ![error.domain isEqual:@"com.apple.reminderkit"] || error.code!=-3000)return EncodeFlag(FlagFailure(@"snapshot_changed"));
        error=nil;id list=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchListWithObjectID:error:"),listID,&error);
        if(error || !UUIDEqual(ObjectUUID(list,@"REMCDList"),identity[@"list_id"]) || !UUIDEqual(ObjectUUID(ObjectValue(list,@"account"),@"REMCDAccount"),identity[@"source_id"]))return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
        id save=((id(*)(id,SEL,id))objc_msgSend)([saveClass alloc],NSSelectorFromString(@"initWithStore:"),store),change=((id(*)(id,SEL,id))objc_msgSend)(save,NSSelectorFromString(@"updateList:"),list),context=ObjectValue(change,@"sectionsContextChangeItem");
        if(!context || !((id(*)(id,SEL,id,id,id))objc_msgSend)(save,NSSelectorFromString(@"addListSectionWithDisplayName:toListSectionContextChangeItem:listSectionObjectID:"),request[@"title"],context,sectionID))return EncodeFlag(FlagFailure(@"unsupported_section_write"));
        if(!enterMutation())return EncodeFlag(FlagFailure(@"native_dispatch_cancelled"));
        dispatched=YES;error=nil;BOOL saved=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(save,NSSelectorFromString(@"saveSynchronouslyWithError:"),&error);
        if(!saved||error)return EncodeFlag(@{@"protocol_version":@1,@"error":@"mutation_failed_or_uncertain",@"mutation_dispatched":@YES});
        return EncodeFlag(@{@"protocol_version":@1,@"result":@{@"id":newID,@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"saved_locally":@YES}});
    }@catch(NSException *exception){return EncodeFlag(@{@"protocol_version":@1,@"error":dispatched?@"mutation_failed_or_uncertain":@"private_metadata_unavailable",@"mutation_dispatched":@(dispatched)});}
}
// Rename one previously inventoried ordinary section after exact scope checks.
NSData *ICloudRemindersRenameSection(NSData *data,BOOL (^enterMutation)(void)){
    __block BOOL dispatched=NO;
    @try {
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=5 || !enterMutation || ![request[@"title"] isKindOfClass:NSString.class] || ![request[@"title"] length] || [request[@"title"] length]>1000 || ![request[@"expected_sections"] isKindOfClass:NSArray.class])return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSString *sectionID=request[@"section_id"];NSUUID *uuid=[sectionID isKindOfClass:NSString.class]?[[NSUUID alloc]initWithUUIDString:sectionID]:nil;if(!uuid||!UUIDEqual(uuid.UUIDString,sectionID))return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSDictionary *identity=@{@"source_id":request[@"source_id"]?:[NSNull null],@"list_id":request[@"list_id"]?:[NSNull null]};NSData *bytes=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];id observed=[NSJSONSerialization JSONObjectWithData:ICloudRemindersListSections(bytes) options:0 error:NULL];
        if(![observed[@"sections"] isEqual:request[@"expected_sections"]])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        NSArray *matches=[observed[@"sections"] filteredArrayUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(id row,NSDictionary *bindings){return UUIDEqual(row[@"id"],sectionID);}]];if(matches.count!=1 || [matches[0][@"title"] isEqual:request[@"title"]])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        for(NSDictionary *row in observed[@"sections"])if([row[@"title"] isEqual:request[@"title"]])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        Class ids=NSClassFromString(@"REMObjectID"),storeClass=NSClassFromString(@"REMStore"),saveClass=NSClassFromString(@"REMSaveRequest");
        if(!MethodMatches(storeClass,@"fetchListSectionWithObjectID:error:",@"@@:@^@",NO)||!MethodMatches(saveClass,@"initWithStore:",@"@@:@",NO)||!MethodMatches(saveClass,@"updateListSection:",@"@@:@",NO)||!MethodMatches(saveClass,@"saveSynchronouslyWithError:",@"B@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_section_write"));
        id store=[storeClass new];NSURL *url=[NSURL URLWithString:[@"x-apple-reminderkit://REMCDListSection/" stringByAppendingString:sectionID]];id oid=((id(*)(id,SEL,id))objc_msgSend)((id)ids,NSSelectorFromString(@"objectIDWithURL:"),url);NSError *error=nil;id section=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchListSectionWithObjectID:error:"),oid,&error);id list=ObjectValue(section,@"list"),storage=ObjectValue(section,@"storage");
        if(error || !UUIDEqual(ObjectUUID(section,@"REMCDListSection"),sectionID) || !UUIDEqual(ObjectUUID(list,@"REMCDList"),identity[@"list_id"]) || !UUIDEqual(ObjectUUID(ObjectValue(list,@"account"),@"REMCDAccount"),identity[@"source_id"]) || ![ObjectValue(storage,@"displayName") isEqual:matches[0][@"title"]])return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
        id save=((id(*)(id,SEL,id))objc_msgSend)([saveClass alloc],NSSelectorFromString(@"initWithStore:"),store),change=((id(*)(id,SEL,id))objc_msgSend)(save,NSSelectorFromString(@"updateListSection:"),section);
        SEL setter=NSSelectorFromString(@"setDisplayName:");id changeStorage=ObjectValue(change,@"storage");if(!change || !changeStorage || ![change respondsToSelector:setter] || !MethodMatches([changeStorage class],@"setDisplayName:",@"v@:@",NO))return EncodeFlag(FlagFailure(@"unsupported_section_write"));((void(*)(id,SEL,id))objc_msgSend)(change,setter,request[@"title"]);
        if(![ObjectValue(changeStorage,@"displayName") isEqual:request[@"title"]])return EncodeFlag(FlagFailure(@"unsupported_section_write"));
        if(!enterMutation())return EncodeFlag(FlagFailure(@"native_dispatch_cancelled"));dispatched=YES;error=nil;BOOL saved=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(save,NSSelectorFromString(@"saveSynchronouslyWithError:"),&error);
        if(!saved||error)return EncodeFlag(@{@"protocol_version":@1,@"error":@"mutation_failed_or_uncertain",@"mutation_dispatched":@YES});return EncodeFlag(@{@"protocol_version":@1,@"result":@{@"id":sectionID,@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"saved_locally":@YES}});
    }@catch(NSException *exception){return EncodeFlag(@{@"protocol_version":@1,@"error":dispatched?@"mutation_failed_or_uncertain":@"private_metadata_unavailable",@"mutation_dispatched":@(dispatched)});}
}
// Read the provider's section relationship for one already scoped reminder.
NSData *ICloudRemindersReadSection(NSData *data){
    @try {
        NSDictionary *failure=nil;id store=nil;id reminder=SelectedReminderWithStore(data,&failure,&store);if(!reminder||!store)return EncodeFlag(failure?:FlagFailure(@"private_store_unavailable"));
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];Class viewClass=NSClassFromString(@"REMListSectionsDataView");
        if(!MethodMatches(viewClass,@"initWithStore:",@"@@:@",NO)||!MethodMatches(viewClass,@"fetchListSectionWithReminderID:error:",@"@@:@^@",NO))return EncodeFlag(FlagFailure(@"unsupported_section_metadata"));
        id view=((id(*)(id,SEL,id))objc_msgSend)([viewClass alloc],NSSelectorFromString(@"initWithStore:"),store),objectID=ObjectValue(reminder,@"remObjectID");NSError *error=nil;
        if(!view||!objectID)return EncodeFlag(FlagFailure(@"unsupported_section_metadata"));
        id section=((id(*)(id,SEL,id,NSError **))objc_msgSend)(view,NSSelectorFromString(@"fetchListSectionWithReminderID:error:"),objectID,&error);
        id sectionValue=[NSNull null];NSString *status=@"provider_not_reported";
        if(error){if(![error.domain isEqual:@"com.apple.reminderkit"]||error.code!=-3009)return EncodeFlag(FlagFailure(@"unsupported_section_metadata"));}
        else {
            NSString *sectionID=ObjectUUID(section,@"REMCDListSection");id list=ObjectValue(section,@"list"),storage=ObjectValue(section,@"storage"),title=ObjectValue(storage,@"displayName"),account=ObjectValue(storage,@"accountID"),listID=ObjectValue(storage,@"listID"),accountUUID=ObjectValue(account,@"uuid"),listUUID=ObjectValue(listID,@"uuid");
            if(!sectionID || ![title isKindOfClass:NSString.class] || [title length]>1000 || !UUIDEqual(ObjectUUID(list,@"REMCDList"),identity[@"list_id"]) || !UUIDEqual(ObjectUUID(ObjectValue(list,@"account"),@"REMCDAccount"),identity[@"source_id"])
               || ![ObjectValue(account,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(listID,@"entityName") isEqual:@"REMCDList"] || ![accountUUID isKindOfClass:NSUUID.class] || ![listUUID isKindOfClass:NSUUID.class]
               || !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([listUUID UUIDString],identity[@"list_id"]))return EncodeFlag(FlagFailure(@"native_identity_mismatch"));
            sectionValue=@{@"id":sectionID,@"title":title};status=@"provider_reported";
        }
        return EncodeFlag(@{@"protocol_version":@1,@"native_metadata":@{@"backend":@"reminderkit-section-v1",@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"section":sectionValue,@"section_status":status,@"coverage":@[@"section"],@"native_metadata_complete":@NO,@"observation_consistency":@"separate_provider_reads"}});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}
// Early reminders are independent of EventKit's ordinary alarms. Read only the
// selected reminder's bounded provider payload; never infer from an empty EK array.
NSData *ICloudRemindersReadEarly(NSData *data){
    @try {
        NSDictionary *failure=nil;id reminder=SelectedReminder(data,&failure);if(!reminder)return EncodeFlag(failure);
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];id storage=ObjectValue(reminder,@"storage");
        if(!storage || !MethodMatches([storage class],@"hasUnfetchedDueDateDeltaAlerts",@"B@:",NO) || !MethodMatches([storage class],@"dueDateDeltaAlertsData",@"@@:",NO))return EncodeFlag(FlagFailure(@"unsupported_early_metadata"));
        BOOL unfetched=((BOOL(*)(id,SEL))objc_msgSend)(storage,NSSelectorFromString(@"hasUnfetchedDueDateDeltaAlerts"));id raw=ObjectValue(storage,@"dueDateDeltaAlertsData");
        NSMutableArray *alerts=[NSMutableArray array];id fingerprint=[NSNull null];
        if(raw){
            if(![raw isKindOfClass:NSData.class] || [raw length]>65536 || ![raw length])return EncodeFlag(FlagFailure(@"unsupported_early_metadata"));
            id payload=[NSJSONSerialization JSONObjectWithData:raw options:0 error:NULL];
            if(![payload isKindOfClass:NSDictionary.class] || ![payload[@"minimumSupportedVersion"] isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)payload[@"minimumSupportedVersion"])==CFBooleanGetTypeID() || [payload[@"minimumSupportedVersion"] doubleValue]!=20230430 || ![payload[@"accountIdentifier"] isKindOfClass:NSString.class] || ![payload[@"reminderIdentifier"] isKindOfClass:NSString.class] || !UUIDEqual(payload[@"accountIdentifier"],identity[@"source_id"]) || !UUIDEqual(payload[@"reminderIdentifier"],identity[@"id"]) || ![payload[@"dueDateDeltaAlerts"] isKindOfClass:NSArray.class] || [payload[@"dueDateDeltaAlerts"] count]>100)return EncodeFlag(FlagFailure(@"unsupported_early_metadata"));
            for(id alert in payload[@"dueDateDeltaAlerts"]){
                if(![alert isKindOfClass:NSDictionary.class])return EncodeFlag(FlagFailure(@"unsupported_early_metadata"));
                id unit=alert[@"dueDateDeltaUnit"],count=alert[@"dueDateDeltaCount"];
                if(![unit isKindOfClass:NSNumber.class] || ![count isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)unit)==CFBooleanGetTypeID() || CFGetTypeID((__bridge CFTypeRef)count)==CFBooleanGetTypeID() || [unit doubleValue]!=[unit integerValue] || [count doubleValue]!=[count integerValue] || [unit integerValue]<0 || [unit integerValue]>100 || [count integerValue]<-100000 || [count integerValue]>100000)return EncodeFlag(FlagFailure(@"unsupported_early_metadata"));
                [alerts addObject:@{@"native_unit":unit,@"native_count":count}];
            }
            unsigned char digest[CC_SHA256_DIGEST_LENGTH];CC_SHA256([raw bytes],(CC_LONG)[raw length],digest);NSMutableString *hex=[NSMutableString stringWithCapacity:64];for(int i=0;i<CC_SHA256_DIGEST_LENGTH;i++)[hex appendFormat:@"%02x",digest[i]];fingerprint=hex;
        }else if(unfetched)return EncodeFlag(FlagFailure(@"unsupported_early_metadata"));
        return EncodeFlag(@{@"protocol_version":@1,@"native_metadata":@{@"backend":@"reminderkit-early-v1",@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"early_alerts":alerts,@"early_payload_fingerprint":fingerprint,@"coverage":@[@"early"],@"native_metadata_complete":@NO,@"observation_consistency":@"separate_provider_reads"}});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}
// Add one observed-empty early offset through the same final mutation gate.
NSData *ICloudRemindersAddEarly(NSData *data,BOOL (^enterMutation)(void)){
    __block BOOL dispatched=NO;
    @try {
        id req=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![req isKindOfClass:NSDictionary.class] || [req count]!=5 || !enterMutation)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        id unit=req[@"unit"],count=req[@"count"];
        if(![unit isKindOfClass:NSNumber.class] || ![count isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)unit)==CFBooleanGetTypeID() || CFGetTypeID((__bridge CFTypeRef)count)==CFBooleanGetTypeID() || [unit doubleValue]!=[unit integerValue] || [count doubleValue]!=[count integerValue] || [unit integerValue]<0 || [unit integerValue]>4 || [count integerValue]>-1 || [count integerValue]<-10000)return EncodeFlag(FlagFailure(@"invalid_early_offset"));
        NSDictionary *identity=@{@"id":req[@"id"]?:[NSNull null],@"source_id":req[@"source_id"]?:[NSNull null],@"list_id":req[@"list_id"]?:[NSNull null]};NSData *bytes=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];NSDictionary *failure=nil;id store=nil;id reminder=SelectedReminderWithStore(bytes,&failure,&store);if(!reminder||!store)return EncodeFlag(failure?:FlagFailure(@"private_store_unavailable"));
        id read=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadEarly(bytes) options:0 error:NULL];id metadata=read[@"native_metadata"];
        if(![metadata isKindOfClass:NSDictionary.class] || metadata[@"early_payload_fingerprint"]!=[NSNull null] || ![metadata[@"early_alerts"] isEqual:@[]])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        Class cls=NSClassFromString(@"REMSaveRequest"),deltaClass=NSClassFromString(@"REMDueDateDeltaInterval");
        if(!MethodMatches(cls,@"initWithStore:",@"@@:@",NO)||!MethodMatches(cls,@"updateReminder:",@"@@:@",NO)||!MethodMatches(cls,@"saveSynchronouslyWithError:",@"B@:^@",NO)||!MethodMatches(deltaClass,@"initWithUnit:count:",@"@@:qq",NO))return EncodeFlag(FlagFailure(@"unsupported_early_write"));
        id save=((id(*)(id,SEL,id))objc_msgSend)([cls alloc],NSSelectorFromString(@"initWithStore:"),store);id change=((id(*)(id,SEL,id))objc_msgSend)(save,NSSelectorFromString(@"updateReminder:"),reminder);id context=ObjectValue(change,@"dueDateDeltaAlertContext");
        if(!context||!MethodMatches([context class],@"addDueDateDeltaAlertWithDueDateDelta:",@"@@:@",NO))return EncodeFlag(FlagFailure(@"unsupported_early_write"));
        id interval=((id(*)(id,SEL,NSInteger,NSInteger))objc_msgSend)([deltaClass alloc],NSSelectorFromString(@"initWithUnit:count:"),[unit integerValue],[count integerValue]);id added=((id(*)(id,SEL,id))objc_msgSend)(context,NSSelectorFromString(@"addDueDateDeltaAlertWithDueDateDelta:"),interval);if(!added)return EncodeFlag(FlagFailure(@"unsupported_early_write"));
        if(!enterMutation())return EncodeFlag(FlagFailure(@"native_dispatch_cancelled"));dispatched=YES;NSError *error=nil;BOOL saved=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(save,NSSelectorFromString(@"saveSynchronouslyWithError:"),&error);
        if(!saved||error)return EncodeFlag(@{@"protocol_version":@1,@"error":@"mutation_failed_or_uncertain",@"mutation_dispatched":@YES});
        return EncodeFlag(@{@"protocol_version":@1,@"result":@{@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"saved_locally":@YES}});
    }@catch(NSException *exception){return EncodeFlag(@{@"protocol_version":@1,@"error":dispatched?@"mutation_failed_or_uncertain":@"private_metadata_unavailable",@"mutation_dispatched":@(dispatched)});}
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

// Report the provider's current-assignment observation without inferring people or permissions.
NSData *ICloudRemindersReadAssignment(NSData *data){
    @try {
        NSDictionary *failure=nil;id reminder=SelectedReminder(data,&failure);if(!reminder)return EncodeFlag(failure);
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(!MethodMatches([reminder class],@"assignmentContext",@"@@:",NO))return EncodeFlag(FlagFailure(@"unsupported_assignment_metadata"));
        id context=ObjectValue(reminder,@"assignmentContext");
        if(!context || !MethodMatches([context class],@"currentAssignment",@"@@:",NO) || !MethodMatches([context class],@"assignments",@"@@:",NO))return EncodeFlag(FlagFailure(@"unsupported_assignment_metadata"));
        id assignments=ObjectValue(context,@"assignments"),current=ObjectValue(context,@"currentAssignment");
        if(![assignments isKindOfClass:NSSet.class] || [assignments count]>100 || (current && ![assignments containsObject:current]))return EncodeFlag(FlagFailure(@"unsupported_assignment_metadata"));
        Class assignmentClass=NSClassFromString(@"REMAssignment");if(!assignmentClass)return EncodeFlag(FlagFailure(@"unsupported_assignment_metadata"));
        for(id assignment in assignments){
            if(![assignment isKindOfClass:assignmentClass])return EncodeFlag(FlagFailure(@"unsupported_assignment_metadata"));
            id account=ObjectValue(assignment,@"accountID"),parent=ObjectValue(assignment,@"reminderID"),accountUUID=ObjectValue(account,@"uuid"),parentUUID=ObjectValue(parent,@"uuid");
            if(![ObjectValue(account,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(parent,@"entityName") isEqual:@"REMCDReminder"]
                || ![accountUUID isKindOfClass:NSUUID.class] || ![parentUUID isKindOfClass:NSUUID.class]
                || !UUIDEqual([accountUUID UUIDString],identity[@"source_id"]) || !UUIDEqual([parentUUID UUIDString],identity[@"id"]))return EncodeFlag(FlagFailure(@"unsupported_assignment_metadata"));
        }
        return EncodeFlag(@{@"protocol_version":@1,@"native_metadata":@{@"backend":@"reminderkit-assignment-v1",@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"current_assignment_reported":@((BOOL)(current!=nil)),@"assignment_record_count":@([assignments count]),@"assignment_metadata_complete":@NO,@"coverage":@[@"assignment"],@"native_metadata_complete":@NO,@"observation_consistency":@"separate_provider_reads"}});
    }@catch(NSException *exception){return EncodeFlag(FlagFailure(@"private_read_exception"));}
}

// Read scoped attachment identities and UTIs without resolving paths or content.
NSData *ICloudRemindersReadAttachments(NSData *data){
    @try {
        NSDictionary *failure=nil;id reminder=SelectedReminder(data,&failure);if(!reminder)return EncodeFlag(failure);
        NSDictionary *identity=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(!MethodMatches([reminder class],@"attachmentContext",@"@@:",NO))return EncodeFlag(FlagFailure(@"unsupported_attachment_metadata"));
        id context=ObjectValue(reminder,@"attachmentContext");
        if(!context || !MethodMatches([context class],@"attachments",@"@@:",NO))return EncodeFlag(FlagFailure(@"unsupported_attachment_metadata"));
        id collection=ObjectValue(context,@"attachments");
        if(![collection isKindOfClass:NSArray.class] || [collection count]>100)return EncodeFlag(FlagFailure(@"unsupported_attachment_metadata"));
        NSDictionary *types=@{@"REMCDAttachment":@"attachment",@"REMCDFileAttachment":@"file",@"REMCDImageAttachment":@"image",@"REMCDURLAttachment":@"url"};
        NSMutableArray *out=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];
        for(id attachment in collection){
            if(!MethodMatches([attachment class],@"uti",@"@@:",NO))return EncodeFlag(FlagFailure(@"unsupported_attachment_metadata"));
            id objectID=ObjectValue(attachment,@"remObjectID"),entity=ObjectValue(objectID,@"entityName"),uuid=ObjectValue(objectID,@"uuid"),account=ObjectValue(attachment,@"accountID"),parent=ObjectValue(attachment,@"reminderID"),uti=ObjectValue(attachment,@"uti");
            if(![entity isKindOfClass:NSString.class] || !types[entity] || ![uuid isKindOfClass:NSUUID.class] || [seen containsObject:[uuid UUIDString]]
                || ![ObjectValue(account,@"entityName") isEqual:@"REMCDAccount"] || ![ObjectValue(parent,@"entityName") isEqual:@"REMCDReminder"]
                || ![ObjectValue(account,@"uuid") isKindOfClass:NSUUID.class] || ![ObjectValue(parent,@"uuid") isKindOfClass:NSUUID.class]
                || !UUIDEqual([ObjectValue(account,@"uuid") UUIDString],identity[@"source_id"]) || !UUIDEqual([ObjectValue(parent,@"uuid") UUIDString],identity[@"id"])
                || (uti && (![uti isKindOfClass:NSString.class] || [uti lengthOfBytesUsingEncoding:NSUTF8StringEncoding]>512)))return EncodeFlag(FlagFailure(@"unsupported_attachment_metadata"));
            [seen addObject:[uuid UUIDString]];[out addObject:@{@"id":[uuid UUIDString],@"kind":types[entity],@"uti":uti?:[NSNull null]}];
        }
        [out sortUsingComparator:^NSComparisonResult(NSDictionary *a,NSDictionary *b){return [a[@"id"] compare:b[@"id"]];}];
        return EncodeFlag(@{@"protocol_version":@1,@"native_metadata":@{@"backend":@"reminderkit-attachments-v1",@"id":identity[@"id"],@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"attachments":out,@"attachment_metadata_complete":@NO,@"coverage":@[@"attachments"],@"native_metadata_complete":@NO,@"observation_consistency":@"separate_provider_reads"}});
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

// Create exactly one child with a preassigned native ID under one scoped parent.
NSData *ICloudRemindersCreateSubtask(NSData *data,BOOL (^enterMutation)(void)){
    __block BOOL dispatched=NO;
    @try{
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class] || [request count]!=6 || !enterMutation)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSString *childID=request[@"child_id"],*title=request[@"title"];NSArray *expectedChildren=request[@"expected_children"];
        if(![expectedChildren isKindOfClass:NSArray.class] || expectedChildren.count>=100 || ![childID isKindOfClass:NSString.class] || ![[NSUUID alloc]initWithUUIDString:childID] || ![title isKindOfClass:NSString.class] || title.length<1 || title.length>1000 || [title lengthOfBytesUsingEncoding:NSUTF8StringEncoding]>4000 || [title rangeOfCharacterFromSet:NSCharacterSet.newlineCharacterSet].location!=NSNotFound)return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSDictionary *identity=@{@"id":request[@"id"] ?: [NSNull null],@"source_id":request[@"source_id"] ?: [NSNull null],@"list_id":request[@"list_id"] ?: [NSNull null]};
        if(UUIDEqual(childID,identity[@"id"]))return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        NSData *identityData=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];NSDictionary *failure=nil;id store=nil;
        id parent=SelectedReminderWithStore(identityData,&failure,&store);if(!parent || !store)return EncodeFlag(failure ?: FlagFailure(@"private_store_unavailable"));
        NSDictionary *observed=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadChildren(identityData) options:0 error:NULL];
        if(![observed[@"native_metadata"][@"child_ids"] isEqual:expectedChildren] || [expectedChildren containsObject:childID])return EncodeFlag(FlagFailure(@"snapshot_changed"));
        Class cls=NSClassFromString(@"REMSaveRequest"),objectClass=NSClassFromString(@"REMObjectID");
        if(!MethodMatches(cls,@"initWithStore:",@"@@:@",NO) || !MethodMatches(cls,@"updateReminder:",@"@@:@",NO) || !MethodMatches(cls,@"addReminderWithTitle:toReminderSubtaskContextChangeItem:reminderObjectID:",@"@@:@@@",NO) || !MethodMatches(cls,@"saveSynchronouslyWithError:",@"B@:^@",NO))return EncodeFlag(FlagFailure(@"unsupported_subtask_write"));
        id objectID=((id(*)(id,SEL,id))objc_msgSend)((id)objectClass,NSSelectorFromString(@"objectIDWithURL:"),[NSURL URLWithString:[@"x-apple-reminderkit://REMCDReminder/" stringByAppendingString:childID]]);
        id objectUUID=ObjectValue(objectID,@"uuid");if(![objectUUID isKindOfClass:NSUUID.class] || !UUIDEqual([objectUUID UUIDString],childID) || ![ObjectValue(objectID,@"entityName") isEqual:@"REMCDReminder"])return EncodeFlag(FlagFailure(@"invalid_public_identity"));
        if(!MethodMatches([store class],@"fetchReminderIncludingMarkedForDeleteWithObjectID:error:",@"@@:@^@",NO))return EncodeFlag(FlagFailure(@"unsupported_subtask_write"));
        NSError *lookupError=nil;id existing=((id(*)(id,SEL,id,NSError **))objc_msgSend)(store,NSSelectorFromString(@"fetchReminderIncludingMarkedForDeleteWithObjectID:error:"),objectID,&lookupError);
        if(existing || (lookupError && !([lookupError.domain isEqual:@"com.apple.reminderkit"] && lookupError.code==-3000)))return EncodeFlag(FlagFailure(@"snapshot_changed"));
        id save=((id(*)(id,SEL,id))objc_msgSend)([cls alloc],NSSelectorFromString(@"initWithStore:"),store);
        id change=((id(*)(id,SEL,id))objc_msgSend)(save,NSSelectorFromString(@"updateReminder:"),parent),context=ObjectValue(change,@"subtaskContext");
        if(!context)return EncodeFlag(FlagFailure(@"unsupported_subtask_write"));
        id child=((id(*)(id,SEL,id,id,id))objc_msgSend)(save,NSSelectorFromString(@"addReminderWithTitle:toReminderSubtaskContextChangeItem:reminderObjectID:"),title,context,objectID);
        if(!child)return EncodeFlag(FlagFailure(@"unsupported_subtask_write"));
        if(!enterMutation())return EncodeFlag(FlagFailure(@"native_dispatch_cancelled"));
        dispatched=YES;NSError *error=nil;BOOL saved=((BOOL(*)(id,SEL,NSError **))objc_msgSend)(save,NSSelectorFromString(@"saveSynchronouslyWithError:"),&error);
        if(!saved || error)return EncodeFlag(@{@"protocol_version":@1,@"error":@"mutation_failed_or_uncertain",@"mutation_dispatched":@YES});
        return EncodeFlag(@{@"protocol_version":@1,@"result":@{@"id":childID,@"source_id":identity[@"source_id"],@"list_id":identity[@"list_id"],@"parent_id":identity[@"id"],@"saved_locally":@YES}});
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
