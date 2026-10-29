"""Execute the shipped template constructor guard without opening Apple stores."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
static NSString *mode;static int saves=0,gates=0;static NSDictionary *expectedGraph,*expectedSections;static NSArray *expectedTemplates;
@interface FID:NSObject<NSCopying>
@property NSString *entityName;@property NSUUID *uuid;+(id)objectIDWithURL:(NSURL*)url;
@end
@implementation FID
+(id)objectIDWithURL:(NSURL*)url{FID *v=[FID new];v.entityName=url.host;v.uuid=[[NSUUID alloc]initWithUUIDString:url.lastPathComponent];return v;}
-(id)copyWithZone:(NSZone*)zone{return self;}
@end
static FID *ident(NSString *entity,NSString *uuid){return [FID objectIDWithURL:[NSURL URLWithString:[NSString stringWithFormat:@"x-apple-reminderkit://%@/%@",entity,uuid]]];}
@interface FConfig:NSObject
@property FID *sourceListID;@property BOOL shouldSaveCompleted;
-(id)initWithSourceListID:(id)identity shouldSaveCompleted:(BOOL)completed;
@end
@implementation FConfig
-(id)initWithSourceListID:(id)identity shouldSaveCompleted:(BOOL)completed{self=[super init];self.sourceListID=identity;self.shouldSaveCompleted=[mode isEqual:@"completed_copy"]?YES:completed;return self;}
@end
@interface FStorage:NSObject<NSCopying>
@property FID *remObjectID;@property FID *accountID;@property FID *parentAccountID;@property NSString *titleAsString;@property NSString *notesAsString;@property NSString *name;
@property NSArray *attachments;@property NSArray *alarms;@property NSArray *recurrenceRules;@property id assignments;@property id contactHandles;@property id userActivity;@property id startDateComponents;@property id dueDateComponents;@property id timeZone;
@property NSData *titleDocumentData;@property NSData *notesDocumentData;@property(getter=isCompleted) BOOL completed;@property long long flagged;@property BOOL isUrgentStateEnabledForCurrentUser;@property long long priority;@property long long minimumSupportedVersion;@property FConfig *configuration;@property id publicLink;@property NSSet *sectionIDsToUndelete;
@end
@implementation FStorage
-(id)copyWithZone:(NSZone*)zone{if([mode isEqual:@"alias_copy"])return self;FStorage *v=[FStorage new];for(NSString *key in @[@"remObjectID",@"accountID",@"parentAccountID",@"titleAsString",@"notesAsString",@"name",@"attachments",@"alarms",@"recurrenceRules",@"assignments",@"contactHandles",@"userActivity",@"startDateComponents",@"dueDateComponents",@"timeZone",@"titleDocumentData",@"notesDocumentData",@"priority",@"minimumSupportedVersion",@"configuration",@"publicLink",@"sectionIDsToUndelete"])[v setValue:[self valueForKey:key]forKey:key];return v;}
-(BOOL)isEqual:(id)value{if(![value isKindOfClass:FStorage.class])return NO;FStorage *v=value;return self.remObjectID==v.remObjectID && self.accountID==v.accountID && self.priority==v.priority && ((self.name==v.name)||[self.name isEqual:v.name]) && ((self.titleDocumentData==v.titleDocumentData)||[self.titleDocumentData isEqual:v.titleDocumentData]);}
@end
@interface FObject:NSObject
@property FID *remObjectID;@property FStorage *storage;
@end
@implementation FObject @end
@interface FChange:NSObject
@property FStorage *storage;@property NSSet *changedKeys;
@end
@implementation FChange @end
@interface FWrap:NSObject
@property id valueWithoutPerformingCopy;
@end
@implementation FWrap @end
static FObject *account,*list,*task;
@interface FSave:NSObject
@property FChange *ac;@property FChange *change;
-(id)initWithStore:(id)store;-(id)updateAccount:(id)value;
-(id)addTemplateWithName:(id)name configuration:(id)config toAccountChangeItem:(id)ac templateObjectID:(id)identity;
-(BOOL)saveSynchronouslyWithError:(NSError**)error;
-(id)trackedAccountChangeItems;-(id)trackedTemplateChangeItems;-(id)trackedListChangeItems;-(id)trackedListSectionChangeItems;-(id)trackedReminderChangeItems;-(id)trackedSmartListChangeItems;-(id)trackedSmartListSectionChangeItems;-(id)trackedTemplateSectionChangeItems;
@end
@implementation FSave
-(id)initWithStore:(id)store{return [super init];}
-(id)updateAccount:(FObject*)value{self.ac=[FChange new];self.ac.storage=[value.storage copy];self.ac.changedKeys=[NSSet set];return self.ac;}
-(id)addTemplateWithName:(id)name configuration:(id)config toAccountChangeItem:(FChange*)ac templateObjectID:(FID*)identity{
 self.change=[FChange new];FStorage *s=[FStorage new];self.change.storage=s;s.remObjectID=identity;s.accountID=account.remObjectID;s.parentAccountID=account.remObjectID;s.name=name;s.configuration=config;s.sectionIDsToUndelete=[NSSet set];
 NSMutableSet *keys=[NSMutableSet setWithArray:@[@"showingLargeAttachments",@"shouldUpdateSectionsOrdering",@"accountID",@"sectionIDsToUndelete",@"configuration",@"minimumSupportedVersion",@"parentAccountID",@"unsavedManualOrdering",@"name",@"objectID"]];self.change.changedKeys=keys;
 if([mode isEqual:@"extra_key"])[keys addObject:@"unreviewed"];
 if([mode isEqual:@"account_key"])ac.changedKeys=[NSSet setWithObject:@"name"];
 if([mode isEqual:@"account_storage"])ac.storage.name=@"changed silently";
 if([mode isEqual:@"wrong_parent"])s.parentAccountID=list.remObjectID;
 if([mode isEqual:@"wrong_id"])s.remObjectID=list.remObjectID;
 if([mode isEqual:@"wrong_version"])s.minimumSupportedVersion=1;
 if([mode isEqual:@"link"])s.publicLink=@"never fetched";
 if([mode isEqual:@"undelete"])s.sectionIDsToUndelete=[NSSet setWithObject:list.remObjectID];
 if([mode isEqual:@"source_drift"])task.storage.priority=9;
 return self.change;
}
-(id)trackedAccountChangeItems{FWrap *w=[FWrap new];w.valueWithoutPerformingCopy=[mode isEqual:@"wrong_wrapper"]?[NSObject new]:self.ac;return @{account.remObjectID:w};}
-(id)trackedTemplateChangeItems{FWrap *w=[FWrap new];w.valueWithoutPerformingCopy=self.change;return @{self.change.storage.remObjectID:w};}
-(id)trackedListChangeItems{return @{};}-(id)trackedListSectionChangeItems{return @{};}-(id)trackedReminderChangeItems{return [mode isEqual:@"unrelated_task"]?@{@"unreviewed":@1}:@{};}-(id)trackedSmartListChangeItems{return @{};}-(id)trackedSmartListSectionChangeItems{return @{};}-(id)trackedTemplateSectionChangeItems{return @{};}
-(BOOL)saveSynchronouslyWithError:(NSError**)error{saves++;if([mode isEqual:@"save_error"]){*error=[NSError errorWithDomain:@"synthetic"code:1 userInfo:nil];return NO;}return YES;}
@end
static NSData *ICloudRemindersListHierarchy(NSData *data){return EncodeFlag([mode isEqual:@"graph_drift"]?@{@"nodes":@[]}:expectedGraph);}
static NSData *ICloudRemindersListSections(NSData *data){return EncodeFlag(expectedSections);}
static NSData *ICloudRemindersListTemplates(NSData *data){return EncodeFlag(@{@"templates":expectedTemplates,@"done":@YES});}
static id SelectedGroupAccount(NSString *source,id __strong *store,NSDictionary **failure){if(!UUIDEqual(source,account.remObjectID.uuid.UUIDString)){*failure=FlagFailure(@"foreign");return nil;}*store=[NSObject new];return account;}
static id ScopedGroupList(id store,NSString *source,NSString *identity){return UUIDEqual(identity,list.remObjectID.uuid.UUIDString)?list:nil;}
static BOOL LifecycleListPrivate(id object,NSString *source,NSString *identity){return ![mode isEqual:@"shared"] && object==list;}
static id SelectedReminder(NSData *data,NSDictionary **failure){id req=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];return UUIDEqual(req[@"id"],task.remObjectID.uuid.UUIDString)?task:nil;}
static BOOL LifecycleGraphMatches(NSDictionary *observed,NSArray *nodes,NSString *source,NSString *listID){return [observed[@"source_id"]isEqual:source] && [observed[@"list_id"]isEqual:listID] && [observed[@"nodes"]isEqual:nodes];}
'''
MAIN=r'''
int main(int argc,const char**argv){@autoreleasepool{
 mode=[NSString stringWithUTF8String:argv[1]];NSString *source=@"11111111-1111-4111-8111-111111111111",*listID=@"22222222-2222-4222-8222-222222222222",*taskID=@"33333333-3333-4333-8333-333333333333",*templateID=@"44444444-4444-4444-8444-444444444444";
 account=[FObject new];account.remObjectID=ident(@"REMCDAccount",source);account.storage=[FStorage new];account.storage.remObjectID=account.remObjectID;
 list=[FObject new];list.remObjectID=ident(@"REMCDList",listID);task=[FObject new];task.remObjectID=ident(@"REMCDReminder",taskID);task.storage=[FStorage new];task.storage.remObjectID=task.remObjectID;task.storage.accountID=account.remObjectID;task.storage.titleAsString=@"Synthetic source";task.storage.notesAsString=@"";task.storage.attachments=@[];task.storage.alarms=@[];task.storage.recurrenceRules=@[];task.storage.titleDocumentData=[NSData data];task.storage.notesDocumentData=[NSData data];
 expectedGraph=@{@"source_id":source,@"list_id":listID,@"nodes":@[@{@"id":taskID,@"parent_id":NSNull.null}],@"data_mutated":@NO};expectedSections=@{@"sections":@[]};expectedTemplates=@[];
 NSMutableDictionary *before=[@{@"source_scope":@{},@"source_graph":@{@"nodes":@[@{@"id":taskID,@"parent_id":NSNull.null,@"completed":@NO}]},@"sections":expectedSections,@"items":@[@{@"item":@{@"id":taskID,@"title":@"Synthetic source",@"notes":@"",@"priority":@0}}],@"templates":@[]}mutableCopy];
 NSMutableDictionary *request=[@{@"source_id":source,@"list_id":listID,@"id":templateID,@"title":@"Synthetic template",@"before":before}mutableCopy];
 if([mode isEqual:@"attachment"])task.storage.attachments=@[@1];if([mode isEqual:@"alarm"])task.storage.alarms=@[@1];if([mode isEqual:@"recurrence"])task.storage.recurrenceRules=@[@1];if([mode isEqual:@"body"])task.storage.notesAsString=@"unreviewed";if([mode isEqual:@"due"])task.storage.dueDateComponents=@1;
 if([mode isEqual:@"completed"])task.storage.completed=YES;if([mode isEqual:@"flagged"])task.storage.flagged=YES;if([mode isEqual:@"urgent"])task.storage.isUrgentStateEnabledForCurrentUser=YES;
 if([mode isEqual:@"foreign_source"])request[@"source_id"]=listID;
 if([mode isEqual:@"extra_field"])request[@"sql"]=@"never";
 if([mode isEqual:@"duplicate_title"])expectedTemplates=@[@{@"id":templateID,@"title":@"Synthetic template"}];
 id response=[NSJSONSerialization JSONObjectWithData:ICloudRemindersCreateTemplate(EncodeFlag(request),^BOOL{gates++;return ![mode isEqual:@"closed"];})options:0 error:NULL];NSData *output=EncodeFlag(@{@"response":response,@"gate_calls":@(gates),@"save_calls":@(saves)});fwrite(output.bytes,1,output.length,stdout);
}}
'''
def run():
 source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();prefix=source[:source.index('static BOOL FlagReadInterface(void)')].replace('#import "reminders-model.h"','#import <Foundation/Foundation.h>');support=source[source.index('static id ObjectValue'):source.index('static id SelectedFailure')];tracked=source[source.index('static BOOL TrackedGroupObject'):source.index('NSData *ICloudRemindersCreateRootGroup')];writer=source[source.index('// Create one private template;'):]
 for real,fake in(('REMSaveRequest','FSave'),('REMObjectID','FID'),('REMTemplateConfiguration','FConfig')):writer=writer.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
 with tempfile.TemporaryDirectory(prefix='template-create-native-')as directory:
  path=Path(directory);(path/'contract.m').write_text(prefix+support+FIXTURE+tracked+writer+MAIN);result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(path/'contract.m'),'-o',str(path/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
  def call(mode):return json.loads(subprocess.run([str(path/'contract'),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
  for mode in('valid','closed','save_error'):
   value=call(mode);assert value['gate_calls']==1,value;assert value['save_calls']==(0 if mode=='closed'else 1),value
   if mode=='valid':assert value['response']['result']['saved_locally']is True
   if mode=='save_error':assert value['response']['mutation_dispatched']is True
  for mode in('attachment','alarm','recurrence','body','due','completed','flagged','urgent','foreign_source','extra_field','duplicate_title','graph_drift','shared','extra_key','account_key','account_storage','wrong_parent','wrong_id','wrong_version','link','undelete','source_drift','completed_copy','alias_copy','wrong_wrapper','unrelated_task'):
   value=call(mode);assert value['gate_calls']==0 and value['save_calls']==0 and value['response']['error'],(mode,value)
 return {'status':'passed','native_store_opened':False,'exact_template_and_unchanged_account_stage':True,'closed_gate_and_unsupported_content_refuse':True,'failed_save_retains_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
