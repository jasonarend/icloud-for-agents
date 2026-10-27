// Fixed optional native model operation. No caller-selected selectors, SQL or paths.
#import "notes-model.h"
#import <CoreData/CoreData.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <dlfcn.h>
#import <sqlite3.h>
#import <sys/stat.h>
#import <unistd.h>

static NSData *Encode(NSDictionary *value) {
    return [NSJSONSerialization dataWithJSONObject:value options:0 error:NULL] ?: [NSData data];
}
static NSDictionary *Failure(NSString *reason,BOOL dispatched,NSString *stage) {
    return @{@"protocol_version":@1,@"error":@"notes_model_refused",@"category":reason,@"mutation_dispatched":@(dispatched),@"diagnostic_stage":stage};
}
static BOOL ModelAvailable(void) {
    if(NSProcessInfo.processInfo.operatingSystemVersion.majorVersion!=27)return NO;
    if(!dlopen("/System/Library/PrivateFrameworks/NotesShared.framework/NotesShared",RTLD_LAZY|RTLD_LOCAL))return NO;
    Class context=NSClassFromString(@"ICNoteContext"),folder=NSClassFromString(@"ICFolder");
    if(!context || !folder || !class_getClassMethod(context,NSSelectorFromString(@"startSharedContextWithOptions:")) || !class_getClassMethod(context,NSSelectorFromString(@"sharedContext")) || !class_getInstanceMethod(context,NSSelectorFromString(@"managedObjectContext")))return NO;
    for(NSString *name in @[@"setTitle:",@"isSystemFolder",@"isSmartFolder",@"isSharedViaICloud"])
        if(!class_getInstanceMethod(folder,NSSelectorFromString(name)))return NO;
    return YES;
}
NSData *ICloudNotesModelStatus(void) {
    return Encode(@{@"protocol_version":@1,@"service":@"notes",@"backend":@"notes-shared-folder-v1",@"interface_available":@(ModelAvailable()),@"context_open_requested":@NO,@"data_access":@"unverified",@"application_writes_enabled":@NO});
}
// Fixed schema-header read only: no model context, content query or OS grant.
static NSString *NotesStorePath(void) {
    return [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"];
}
NSData *ICloudNotesStoreStatus(void) {
    NSMutableDictionary *result=[@{@"protocol_version":@1,@"service":@"notes",@"store_read_access":@"unverified",@"model_context_opened":@NO,@"application_writes_enabled":@NO,@"model_write_access":@"unverified",@"settings_path":@"System Settings > Privacy & Security > Files & Folders",@"requester_bundle_id":NSBundle.mainBundle.bundleIdentifier ?: @"unavailable"} mutableCopy];
    NSInteger major=NSProcessInfo.processInfo.operatingSystemVersion.majorVersion;
    result[@"os_major"]=@(major);
    if(major!=27) {result[@"store_read_access"]=@"unsupported_os";return Encode(result);}
    result[@"automatic_prompt_expected"]=@NO;
    NSString *path=NotesStorePath();struct stat info;
    if(lstat(path.fileSystemRepresentation,&info)!=0) {result[@"store_read_access"]=@"denied_or_unavailable";return Encode(result);}
    if(!S_ISREG(info.st_mode) || info.st_uid!=getuid() || (info.st_mode&0022)!=0) {result[@"store_read_access"]=@"unsafe_store_file";return Encode(result);}
    sqlite3 *database=NULL;int code=sqlite3_open_v2(path.fileSystemRepresentation,&database,SQLITE_OPEN_READONLY|SQLITE_OPEN_FULLMUTEX,NULL);
    if(code==SQLITE_OK && sqlite3_db_readonly(database,"main")==1) {
        sqlite3_busy_timeout(database,1000);sqlite3_stmt *statement=NULL;
        code=sqlite3_prepare_v2(database,"PRAGMA schema_version",-1,&statement,NULL);
        if(code==SQLITE_OK)code=sqlite3_step(statement);
        result[@"store_read_access"]=code==SQLITE_ROW ? @"available":@"denied_or_unavailable";
        if(statement)sqlite3_finalize(statement);
    } else {result[@"store_read_access"]=@"denied_or_unavailable";}
    result[@"sqlite_code"]=@(code);
    if(database)sqlite3_close(database);
    return Encode(result);
}
static BOOL BoolInterface(id object,NSString *name) {
    Method method=object?class_getInstanceMethod([object class],NSSelectorFromString(name)):NULL;
    if(!method || method_getNumberOfArguments(method)!=2)return NO;
    char *type=method_copyReturnType(method);BOOL valid=type && strcmp(type,"B")==0;free(type);return valid;
}
static BOOL Flag(id object,NSString *name) {
    SEL selector=NSSelectorFromString(name);
    return ((BOOL(*)(id,SEL))objc_msgSend)(object,selector);
}
static NSString *Identity(NSManagedObject *object) {return object.objectID.URIRepresentation.absoluteString;}
static BOOL FolderAllowed(NSManagedObject *object,NSString *source,BOOL root) {
    if(![object.entity.name isEqual:@"ICFolder"] || ![object.entity.relationshipsByName[@"account"].destinationEntity.name isEqual:@"ICAccount"] || !object.entity.attributesByName[@"title"] || !object.entity.attributesByName[@"folderType"])return NO;
    if(!BoolInterface(object,@"isSystemFolder") || !BoolInterface(object,@"isSmartFolder") || !BoolInterface(object,@"isSharedViaICloud"))return NO;
    NSManagedObject *account=[object valueForKey:@"account"];
    if(![Identity(account) isEqual:source] || [[object valueForKey:@"markedForDeletion"] boolValue] || [[object valueForKey:@"folderType"] integerValue]!=0 || Flag(object,@"isSmartFolder") || Flag(object,@"isSharedViaICloud") || (root && Flag(object,@"isSystemFolder")))return NO;
    return YES;
}
static BOOL Inventory(NSManagedObject *folder,NSString *source,NSUInteger depth,NSMutableArray *folders,NSMutableArray *notes,NSMutableSet *seen) {
    if(depth>=32 || folders.count>=100 || !FolderAllowed(folder,source,YES) || [seen containsObject:folder.objectID])return NO;
    [seen addObject:folder.objectID];NSManagedObject *parent=[folder valueForKey:@"parent"];
    NSString *title=[folder valueForKey:@"title"];
    if(![title isKindOfClass:NSString.class] || title.length>1000)return NO;
    [folders addObject:@{@"id":Identity(folder),@"title":title,@"source_id":source,@"parent_id":parent?Identity(parent):source,@"shared":@NO,@"is_default":@NO}];
    for(NSManagedObject *note in [folder valueForKey:@"notes"]) {
        if([[note valueForKey:@"markedForDeletion"] boolValue])continue;
        if(![note.entity.name isEqual:@"ICNote"] || ![[note valueForKey:@"folder"] isEqual:folder] || notes.count>=200)return NO;
        [notes addObject:@{@"id":Identity(note),@"folder_id":Identity(folder)}];
    }
    for(NSManagedObject *child in [folder valueForKey:@"children"]) {
        if([[child valueForKey:@"markedForDeletion"] boolValue])continue;
        if(![[child valueForKey:@"parent"] isEqual:folder] || !Inventory(child,source,depth+1,folders,notes,seen))return NO;
    }
    return YES;
}
static NSArray *Sorted(NSArray *rows) {return [rows sortedArrayUsingComparator:^NSComparisonResult(NSDictionary *a,NSDictionary *b){return [a[@"id"] compare:b[@"id"] options:NSLiteralSearch];}];}

NSData *ICloudNotesModelRename(NSData *data,BOOL (^enterMutation)(void)) {
    __block BOOL dispatched=NO;__block NSManagedObjectContext *moc=nil;__block NSDictionary *answer=nil;__block NSString *stage=@"request_validation";
    @try {
        NSDictionary *request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if(![request isKindOfClass:NSDictionary.class])return Encode(Failure(@"invalid_request",NO,stage));
        NSDictionary *change=request[@"change"];
        if(![change isKindOfClass:NSDictionary.class])return Encode(Failure(@"invalid_request",NO,stage));
        NSDictionary *fields=change[@"fields"],*before=change[@"before"];
        if(![fields isKindOfClass:NSDictionary.class] || ![before isKindOfClass:NSDictionary.class])return Encode(Failure(@"invalid_request",NO,stage));
        NSDictionary *expected=before[@"inventory"],*role=before[@"role"];
        NSString *source=change[@"source_id"],*identity=change[@"id"],*title=fields[@"title"];
        NSArray *sources=request[@"source_ids"];
        if(![change isKindOfClass:NSDictionary.class] || ![fields isKindOfClass:NSDictionary.class] || ![before isKindOfClass:NSDictionary.class] || ![expected isKindOfClass:NSDictionary.class] || ![role isKindOfClass:NSDictionary.class] || ![source isKindOfClass:NSString.class] || ![identity isKindOfClass:NSString.class] || ![sources isKindOfClass:NSArray.class] || sources.count!=1 || ![sources[0] isEqual:source] || ![change[@"folder_id"] isEqual:identity] || ![change[@"operation"] isEqual:@"rename_folder"] || ![change[@"writer_backend"] isEqual:@"notes-shared"] || ![title isKindOfClass:NSString.class] || title.length<1 || title.length>1000 || [title rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location!=NSNotFound || ![role[@"folder_id"] isEqual:identity] || ![role[@"source_id"] isEqual:source] || ![role[@"storage_kind"] isEqual:@"regular"] || ![role[@"title"] isKindOfClass:NSString.class])return Encode(Failure(@"invalid_request",NO,stage));
        if(![expected[@"folders"] isKindOfClass:NSArray.class] || ![expected[@"notes"] isKindOfClass:NSArray.class] || [expected[@"folders"] count]<1 || [expected[@"folders"] count]>100 || [expected[@"notes"] count]>200 || expected.count!=3 || fields.count!=1)return Encode(Failure(@"invalid_request",NO,stage));
        stage=@"interface_check";
        if(!ModelAvailable())return Encode(Failure(@"unsupported_edit_target",NO,stage));
        stage=@"context_initialization";
        void (^initializeContext)(void)=^{
            @try {
            Class cls=NSClassFromString(@"ICNoteContext");
            ((void(*)(id,SEL,unsigned long long))objc_msgSend)((id)cls,NSSelectorFromString(@"startSharedContextWithOptions:"),0);
            id context=((id(*)(id,SEL))objc_msgSend)((id)cls,NSSelectorFromString(@"sharedContext"));
            id candidate=[context valueForKey:@"managedObjectContext"];
            if([candidate isKindOfClass:NSManagedObjectContext.class])moc=candidate;
            } @catch(NSException *exception) {answer=Failure(@"notes_model_exception",NO,stage);}
        };
        if(NSThread.isMainThread)initializeContext();else dispatch_sync(dispatch_get_main_queue(),initializeContext);
        if(answer)return Encode(answer);
        if(!moc)return Encode(Failure(@"notes_model_unavailable",NO,stage));
        [moc performBlockAndWait:^{
            @try {
                if(moc.hasChanges) {answer=Failure(@"notes_model_has_unrelated_changes",NO,stage);return;}
                stage=@"resolve_exact_folder";
                NSURL *url=[NSURL URLWithString:identity];NSManagedObjectID *oid=url?[moc.persistentStoreCoordinator managedObjectIDForURIRepresentation:url]:nil;
                NSManagedObject *folder=oid?[moc existingObjectWithID:oid error:NULL]:nil;
                if(!folder || ![Identity(folder) isEqual:identity]) {
                    NSMutableDictionary *failure=[Failure(moc.persistentStoreCoordinator.persistentStores.count ? @"not_found_in_scope":@"notes_model_store_unavailable",NO,stage) mutableCopy];
                    failure[@"store_count"]=@(moc.persistentStoreCoordinator.persistentStores.count);
                    failure[@"has_coordinator"]=@(moc.persistentStoreCoordinator!=nil);
                    failure[@"has_parent_context"]=@(moc.parentContext!=nil);
                    failure[@"uri_resolved"]=@(oid!=nil);answer=failure;return;
                }
                stage=@"validate_folder_role";
                if(!FolderAllowed(folder,source,YES)) {answer=Failure(@"unsupported_edit_target",NO,stage);return;}
                stage=@"compare_persisted_title";
                if(![[folder valueForKey:@"title"] isEqual:role[@"title"]]) {answer=Failure(@"snapshot_changed",NO,stage);return;}
                stage=@"validate_ancestors";
                NSMutableSet *ancestors=[NSMutableSet set];NSManagedObject *ancestor=folder;
                for(NSUInteger depth=0;ancestor;depth++) {
                    if(depth>=32 || [ancestors containsObject:ancestor.objectID] || !FolderAllowed(ancestor,source,NO)) {answer=Failure(@"unsupported_edit_target",NO,stage);return;}
                    [ancestors addObject:ancestor.objectID];ancestor=[ancestor valueForKey:@"parent"];
                }
                stage=@"subtree_inventory";
                NSMutableArray *folders=[NSMutableArray array],*notes=[NSMutableArray array],*siblings=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];
                if(!Inventory(folder,source,0,folders,notes,seen)) {answer=Failure(@"unsupported_edit_target",NO,stage);return;}
                NSManagedObject *parent=[folder valueForKey:@"parent"],*account=[folder valueForKey:@"account"];
                stage=@"validate_siblings";
                if(![expected[@"siblings"] isKindOfClass:NSArray.class] || [expected[@"siblings"] count]>10000) {answer=Failure(@"invalid_request",NO,stage);return;}
                NSMutableSet *peerIDs=[NSMutableSet set];
                for(NSDictionary *row in expected[@"siblings"]) {
                    if(![row isKindOfClass:NSDictionary.class] || row.count!=2 || ![row[@"id"] isKindOfClass:NSString.class] || ![row[@"title"] isKindOfClass:NSString.class]) {answer=Failure(@"invalid_request",NO,stage);return;}
                    NSURL *peerURL=[NSURL URLWithString:row[@"id"]];NSManagedObjectID *peerID=peerURL?[moc.persistentStoreCoordinator managedObjectIDForURIRepresentation:peerURL]:nil;
                    NSManagedObject *peer=peerID?[moc existingObjectWithID:peerID error:NULL]:nil;
                    if(!peer || [peerIDs containsObject:peerID] || [peerID isEqual:folder.objectID] || ![peer.entity.name isEqual:@"ICFolder"] || ![[peer valueForKey:@"account"] isEqual:account] || !(([peer valueForKey:@"parent"]==parent)||[[peer valueForKey:@"parent"] isEqual:parent]) || [[peer valueForKey:@"markedForDeletion"] boolValue] || ![[peer valueForKey:@"title"] isEqual:row[@"title"]]) {answer=Failure(@"snapshot_changed",NO,stage);return;}
                    [peerIDs addObject:peerID];[siblings addObject:row];
                }
                // Check collisions without enumerating unrelated hidden folder metadata.
                stage=@"check_title_collision";
                NSFetchRequest *fetch=[NSFetchRequest fetchRequestWithEntityName:@"ICFolder"];fetch.fetchLimit=2;
                fetch.predicate=[NSPredicate predicateWithFormat:@"account == %@ AND parent == %@ AND markedForDeletion == NO AND title == %@",account,parent,title];
                NSArray *collisions=[moc executeFetchRequest:fetch error:NULL];
                if(!collisions) {answer=Failure(@"notes_model_unavailable",NO,stage);return;}
                for(NSManagedObject *peer in collisions)if(![peer.objectID isEqual:folder.objectID]) {answer=Failure(@"duplicate_folder",NO,stage);return;}
                stage=@"compare_inventory";
                NSMutableDictionary *wanted=[expected mutableCopy];NSMutableArray *wantedFolders=[NSMutableArray array];
                for(NSDictionary *row in expected[@"folders"]) {NSMutableDictionary *copy=[row mutableCopy];if([copy[@"id"] isEqual:identity])copy[@"title"]=role[@"title"];[wantedFolders addObject:copy];}
                wanted[@"folders"]=wantedFolders;
                NSDictionary *observed=@{@"folders":Sorted(folders),@"notes":Sorted(notes),@"siblings":Sorted(siblings)};
                if(![observed isEqual:wanted]) {answer=Failure(@"snapshot_changed",NO,stage);return;}
                stage=@"dispatch_gate";
                if(!enterMutation()) {answer=Failure(@"native_dispatch_cancelled",NO,stage);return;}
                dispatched=YES;
                [folder setValue:title forKey:@"title"];
                NSMutableSet *allowed=[seen mutableCopy];
                for(NSManagedObject *object in moc.registeredObjects) {
                    if([seen containsObject:object.objectID] && object.entity.relationshipsByName[@"cloudState"]) {NSManagedObject *cloud=[object valueForKey:@"cloudState"];if(cloud)[allowed addObject:cloud.objectID];}
                }
                if(moc.insertedObjects.count || moc.deletedObjects.count) { [moc rollback];answer=Failure(@"unexpected_model_effect",YES,stage);return; }
                for(NSManagedObject *object in moc.updatedObjects)if(![allowed containsObject:object.objectID]) {[moc rollback];answer=Failure(@"unexpected_model_effect",YES,stage);return;}
                if(!enterMutation()) {[moc rollback];answer=Failure(@"native_dispatch_cancelled",YES,stage);return;}
                stage=@"save_title";
                NSError *error=nil;
                if(![moc save:&error]) {answer=Failure(@"model_save_failed",YES,stage);return;}
                answer=@{@"protocol_version":@1,@"result":@{@"id":identity,@"source_id":source,@"saved_locally":@YES,@"method":@"notes-shared-folder-v1"}};
            } @catch(NSException *exception) {if(moc.hasChanges)[moc rollback];answer=Failure(@"notes_model_exception",dispatched,stage);}
        }];
        return Encode(answer ?: Failure(@"notes_model_unavailable",dispatched,stage));
    } @catch(NSException *exception) {return Encode(Failure(@"notes_model_exception",dispatched,stage));}
}

// Fixed read-only role check for one exact folder and its ancestors.
NSData *ICloudNotesModelFolderRole(NSData *data) {
    __block NSManagedObjectContext *moc=nil;__block NSDictionary *answer=nil;
    @try {
        id request=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        NSString *identity=[request isKindOfClass:NSDictionary.class]?request[@"folder_id"]:nil;
        NSArray *sources=[request isKindOfClass:NSDictionary.class]?request[@"source_ids"]:nil;
        if(![identity isKindOfClass:NSString.class] || !identity.length || identity.length>1000 || ![sources isKindOfClass:NSArray.class] || sources.count!=1 || ![sources[0] isKindOfClass:NSString.class] || ![sources[0] length] || [sources[0] length]>1000)
            return Encode(Failure(@"invalid_request",NO,@"request_validation"));
        if(!ModelAvailable())return Encode(Failure(@"unsupported_edit_target",NO,@"interface_check"));
        void (^initializeContext)(void)=^{@try{
            Class cls=NSClassFromString(@"ICNoteContext");
            ((void(*)(id,SEL,unsigned long long))objc_msgSend)((id)cls,NSSelectorFromString(@"startSharedContextWithOptions:"),0);
            id context=((id(*)(id,SEL))objc_msgSend)((id)cls,NSSelectorFromString(@"sharedContext"));
            id candidate=[context valueForKey:@"managedObjectContext"];
            if([candidate isKindOfClass:NSManagedObjectContext.class])moc=candidate;
        }@catch(NSException *exception){answer=Failure(@"notes_model_exception",NO,@"context_initialization");}};
        if(NSThread.isMainThread)initializeContext();else dispatch_sync(dispatch_get_main_queue(),initializeContext);
        if(answer)return Encode(answer);
        if(!moc)return Encode(Failure(@"notes_model_unavailable",NO,@"context_initialization"));
        [moc performBlockAndWait:^{@try{
            if(moc.hasChanges){answer=Failure(@"notes_model_has_unrelated_changes",NO,@"resolve_exact_folder");return;}
            NSURL *url=[NSURL URLWithString:identity];NSManagedObjectID *oid=url?[moc.persistentStoreCoordinator managedObjectIDForURIRepresentation:url]:nil;
            NSManagedObject *folder=oid?[moc existingObjectWithID:oid error:NULL]:nil;
            if(!folder || ![Identity(folder) isEqual:identity]){answer=Failure(moc.persistentStoreCoordinator.persistentStores.count?@"not_found_in_scope":@"notes_model_store_unavailable",NO,@"resolve_exact_folder");return;}
            NSMutableSet *seen=[NSMutableSet set];NSManagedObject *current=folder;NSUInteger depth=0;
            while(current){
                if(depth++>=32 || [seen containsObject:current.objectID] || !FolderAllowed(current,sources[0],YES)){
                    answer=Failure(@"unsupported_edit_target",NO,@"validate_folder_role");return;
                }
                [seen addObject:current.objectID];current=[current valueForKey:@"parent"];
            }
            answer=@{@"protocol_version":@1,@"folder_id":identity,@"source_id":sources[0],@"role":@"ordinary_non_system",@"ancestor_count":@(depth-1),@"data_mutated":@NO,@"backend":@"notes-shared-folder-role-v1"};
        }@catch(NSException *exception){answer=Failure(@"notes_model_exception",NO,@"validate_folder_role");}}];
        return Encode(answer ?: Failure(@"notes_model_unavailable",NO,@"resolve_exact_folder"));
    }@catch(NSException *exception){return Encode(Failure(@"notes_model_exception",NO,@"request_validation"));}
}
