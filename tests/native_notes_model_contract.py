"""Compile the fixed writer against an in-memory CoreData fixture, never Notes."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
static BOOL special=NO,shared=NO,smart=NO,trash=NO;
@interface SyntheticFolder:NSManagedObject
@end
@implementation SyntheticFolder
-(BOOL)isSystemFolder{return special;}
-(BOOL)isTrashFolder{return trash;}
-(BOOL)isSmartFolder{return smart;}
-(BOOL)isSharedViaICloud{return shared;}
@end
static NSManagedObjectContext *fixture;
@interface SyntheticContext:NSObject
+(void)startSharedContextWithOptions:(unsigned long long)options;
+(id)sharedContext;
-(id)managedObjectContext;
@end
@implementation SyntheticContext
+(void)startSharedContextWithOptions:(unsigned long long)options{}
+(id)sharedContext{return [SyntheticContext new];}
-(id)managedObjectContext{return fixture;}
@end
static NSAttributeDescription *attr(NSString *name,NSAttributeType type){NSAttributeDescription *a=[NSAttributeDescription new];a.name=name;a.attributeType=type;a.optional=YES;return a;}
static NSRelationshipDescription *rel(NSString *name,NSEntityDescription *dest,BOOL many){NSRelationshipDescription *r=[NSRelationshipDescription new];r.name=name;r.destinationEntity=dest;r.minCount=0;r.maxCount=many?0:1;r.optional=YES;return r;}
static NSString *ID(NSManagedObject *o){return o.objectID.URIRepresentation.absoluteString;}
int main(){@autoreleasepool{
 NSEntityDescription *account=[NSEntityDescription new],*folder=[NSEntityDescription new],*note=[NSEntityDescription new];account.name=@"ICAccount";account.managedObjectClassName=@"NSManagedObject";folder.name=@"ICFolder";folder.managedObjectClassName=@"SyntheticFolder";note.name=@"ICNote";note.managedObjectClassName=@"NSManagedObject";
 NSRelationshipDescription *children=rel(@"children",folder,YES),*parent=rel(@"parent",folder,NO),*notes=rel(@"notes",note,YES),*noteFolder=rel(@"folder",folder,NO);children.inverseRelationship=parent;parent.inverseRelationship=children;notes.inverseRelationship=noteFolder;noteFolder.inverseRelationship=notes;
 folder.properties=@[attr(@"title",NSStringAttributeType),attr(@"folderType",NSInteger64AttributeType),attr(@"markedForDeletion",NSBooleanAttributeType),rel(@"account",account,NO),parent,children,notes];note.properties=@[attr(@"markedForDeletion",NSBooleanAttributeType),noteFolder];
 NSManagedObjectModel *model=[NSManagedObjectModel new];model.entities=@[account,folder,note];NSPersistentStoreCoordinator *psc=[[NSPersistentStoreCoordinator alloc]initWithManagedObjectModel:model];NSCAssert([psc addPersistentStoreWithType:NSInMemoryStoreType configuration:nil URL:nil options:nil error:NULL],@"store");fixture=[[NSManagedObjectContext alloc]initWithConcurrencyType:NSPrivateQueueConcurrencyType];fixture.persistentStoreCoordinator=psc;
 __block NSMutableDictionary *req;__block NSManagedObject *root;
 [fixture performBlockAndWait:^{NSManagedObject *a=[NSEntityDescription insertNewObjectForEntityForName:@"ICAccount" inManagedObjectContext:fixture];root=[NSEntityDescription insertNewObjectForEntityForName:@"ICFolder" inManagedObjectContext:fixture];[root setValue:@"Original" forKey:@"title"];[root setValue:@0 forKey:@"folderType"];[root setValue:@NO forKey:@"markedForDeletion"];[root setValue:a forKey:@"account"];NSCAssert([fixture save:NULL],@"seed");NSString *source=ID(a),*identity=ID(root);req=[@{@"protocol_version":@1,@"service":@"notes",@"action":@"apply",@"source_ids":@[source],@"change":@{@"operation":@"rename_folder",@"writer_backend":@"notes-shared",@"id":identity,@"folder_id":identity,@"source_id":source,@"fields":@{@"title":@"Renamed"},@"before":@{@"role":@{@"folder_id":identity,@"source_id":source,@"storage_kind":@"regular",@"title":@"Original"},@"inventory":@{@"folders":@[@{@"id":identity,@"title":@"Original",@"source_id":source,@"parent_id":source,@"shared":@NO,@"is_default":@NO}],@"notes":@[],@"siblings":@[]}}}} mutableCopy];}];
 NSData *roleData=[NSJSONSerialization dataWithJSONObject:@{@"folder_id":req[@"change"][@"folder_id"],@"source_ids":req[@"source_ids"]} options:0 error:NULL];
 NSDictionary *role=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelFolderRole(roleData) options:0 error:NULL];
 NSCAssert([role[@"role"] isEqual:@"ordinary_non_system"] && ![role[@"data_mutated"] boolValue],@"ordinary role read");
 special=YES;role=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelFolderRole(roleData) options:0 error:NULL];NSCAssert([role[@"category"] isEqual:@"unsupported_edit_target"],@"system folder refused");special=NO;
 special=YES;trash=YES;role=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelFolderRole(roleData) options:0 error:NULL];NSCAssert([role[@"role"] isEqual:@"system_trash"] && ![role[@"data_mutated"] boolValue],@"trash role identified read-only");special=NO;trash=NO;
 trash=YES;role=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelFolderRole(roleData) options:0 error:NULL];NSCAssert([role[@"category"] isEqual:@"unsupported_edit_target"],@"contradictory trash role refused");trash=NO;
 shared=YES;role=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelFolderRole(roleData) options:0 error:NULL];NSCAssert([role[@"category"] isEqual:@"unsupported_edit_target"],@"shared folder refused");shared=NO;
 smart=YES;role=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelFolderRole(roleData) options:0 error:NULL];NSCAssert([role[@"category"] isEqual:@"unsupported_edit_target"],@"smart folder refused");smart=NO;
 [fixture performBlockAndWait:^{[root setValue:@"pending" forKey:@"title"];}];role=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelFolderRole(roleData) options:0 error:NULL];NSCAssert([role[@"category"] isEqual:@"notes_model_has_unrelated_changes"],@"dirty role refused");[fixture performBlockAndWait:^{[fixture rollback];}];
 NSData *data=[NSJSONSerialization dataWithJSONObject:req options:0 error:NULL];__block int entries=0;
 NSDictionary *denied=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelRename(data,^BOOL{entries++;return NO;}) options:0 error:NULL];NSCAssert([denied[@"category"] isEqual:@"native_dispatch_cancelled"] && ![denied[@"mutation_dispatched"] boolValue],@"closed gate");
 [fixture performBlockAndWait:^{NSCAssert([[root valueForKey:@"title"] isEqual:@"Original"] && !fixture.hasChanges,@"no effect");}];
 for(int mode=0;mode<3;mode++) {
  special=mode==0;shared=mode==1;
  if(mode==2)[fixture performBlockAndWait:^{[root setValue:@1 forKey:@"folderType"];[fixture save:NULL];}];
  NSDictionary *refused=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelRename(data,^BOOL{NSCAssert(NO,@"special/shared must not dispatch");return YES;}) options:0 error:NULL];NSCAssert([refused[@"category"] isEqual:@"unsupported_edit_target"],@"role refusal");
  special=NO;shared=NO;
  if(mode==2)[fixture performBlockAndWait:^{[root setValue:@0 forKey:@"folderType"];[fixture save:NULL];}];
 }
 __block int gates=0;
 NSDictionary *cancelled=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelRename(data,^BOOL{return ++gates==1;}) options:0 error:NULL];NSCAssert([cancelled[@"category"] isEqual:@"native_dispatch_cancelled"] && [cancelled[@"mutation_dispatched"] boolValue],@"presave cancellation");
 [fixture performBlockAndWait:^{NSCAssert([[root valueForKey:@"title"] isEqual:@"Original"] && !fixture.hasChanges,@"rollback own title");}];
 [fixture performBlockAndWait:^{[root setValue:@"External pending" forKey:@"title"];}];
 NSDictionary *dirty=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelRename(data,^BOOL{NSCAssert(NO,@"dirty context must not dispatch");return YES;}) options:0 error:NULL];NSCAssert([dirty[@"category"] isEqual:@"notes_model_has_unrelated_changes"],@"unrelated changes refusal");
 [fixture performBlockAndWait:^{NSCAssert([[root valueForKey:@"title"] isEqual:@"External pending"],@"pending untouched");[fixture rollback];}];
 NSDictionary *saved=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelRename(data,^BOOL{entries++;return YES;}) options:0 error:NULL];NSCAssert(saved[@"result"] && entries==3,@"saved exact target");
 [fixture performBlockAndWait:^{NSCAssert([[root valueForKey:@"title"] isEqual:@"Renamed"] && !fixture.hasChanges,@"saved");}];
 NSDictionary *stale=[NSJSONSerialization JSONObjectWithData:ICloudNotesModelRename(data,^BOOL{NSCAssert(NO,@"stale must not dispatch");return YES;}) options:0 error:NULL];NSCAssert([stale[@"category"] isEqual:@"snapshot_changed"] && [stale[@"diagnostic_stage"] isEqual:@"compare_persisted_title"],@"stale with redacted stage");
 puts("in-memory native model: closed gate, pre-save rollback, dirty context, special/shared roles, exact save and stale snapshot passed");return 0;
}}
'''
def run():
    source=(ROOT/'src/icloud_for_agents/native/notes-model.m').read_text()
    begin=source.index('static BOOL ModelAvailable(void) {');end=source.index('NSData *ICloudNotesModelStatus',begin)
    # Compile-time isolated provider: no runtime test bypass in the shipped helper.
    source=source[:begin]+'static BOOL ModelAvailable(void){return YES;}\n'+source[end:]
    source=source.replace('NSClassFromString(@"ICNoteContext")','NSClassFromString(@"SyntheticContext")')
    with tempfile.TemporaryDirectory(prefix='notes-model-contract-') as directory:
        p=Path(directory);(p/'notes-model.h').write_text((ROOT/'src/icloud_for_agents/native/notes-model.h').read_text());(p/'test.m').write_text(source+'\n'+HARNESS)
        subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation','-framework','CoreData','-lsqlite3',str(p/'test.m'),'-o',str(p/'test')],check=True,capture_output=True,text=True)
        result=subprocess.run([str(p/'test')],check=True,capture_output=True,text=True,timeout=15)
    print(result.stdout.strip());return {'status':'passed','provider':'in-memory CoreData','live_notes_access':False}
if __name__=='__main__':run()
