"""Run the shipped grocery category constructor against adversarial native fixtures."""
import json,subprocess,tempfile
from pathlib import Path
from native_grocery_mode_contract import FIXTURE as BASE,ROOT
SECTION=r'''
@interface FSectionStorage:NSObject
@property FID *remObjectID;@property FID *accountID;@property FID *parentID;@property NSString *displayName;@property NSDate *creationDate;@property long long minimumSupportedVersion;
-(id)listID;
@end
@implementation FSectionStorage
-(id)listID{return self.parentID;}
@end
@interface FSectionChange:NSObject
@property FSectionStorage *storage;@property NSMutableSet *changedKeys;
@end
@implementation FSectionChange @end
@interface FStore:NSObject
-(id)fetchListSectionWithObjectID:(id)identity error:(NSError**)error;
@end
@implementation FStore
-(id)fetchListSectionWithObjectID:(id)identity error:(NSError**)error{if([mode isEqual:@"existing_id"])return [NSObject new];*error=[NSError errorWithDomain:[mode isEqual:@"unknown_absence"]?@"synthetic":@"com.apple.reminderkit"code:-3000 userInfo:nil];return nil;}
@end
'''
METHOD=r'''
-(id)addListSectionWithDisplayName:(id)title toListSectionContextChangeItem:(FChange*)context listSectionObjectID:(FID*)identity{
 self.section=[FSectionChange new];FSectionStorage *s=[FSectionStorage new];self.section.storage=s;s.remObjectID=identity;s.accountID=account.remObjectID;s.parentID=list.remObjectID;s.displayName=title;s.creationDate=[NSDate date];self.section.changedKeys=[NSMutableSet setWithArray:@[@"accountID",@"creationDate",@"displayName",@"minimumSupportedVersion",@"objectID",@"parentID"]];
 context.storage.shouldUpdateSectionsOrdering=YES;[context.changedKeys addObject:@"shouldUpdateSectionsOrdering"];
 if([mode isEqual:@"extra_category_key"])[self.section.changedKeys addObject:@"unknown"];
 if([mode isEqual:@"wrong_category_id"])s.remObjectID=list.remObjectID;
 if([mode isEqual:@"wrong_parent"])s.parentID=account.remObjectID;
 if([mode isEqual:@"wrong_title"])s.displayName=@"Unreviewed";
 if([mode isEqual:@"wrong_date"])s.creationDate=nil;
 if([mode isEqual:@"wrong_version"])s.minimumSupportedVersion=-1;
 if([mode isEqual:@"silent_list_content"])context.storage.name=@"Unreviewed";
 if([mode isEqual:@"extra_list_key"])[context.changedKeys addObject:@"name"];
 return self.section;
}
'''
MAIN=r'''
int main(int argc,const char**argv){@autoreleasepool{
mode=[NSString stringWithUTF8String:argv[1]];NSString *source=@"11111111-1111-4111-8111-111111111111",*listing=@"22222222-2222-4222-8222-222222222222",*task=@"33333333-3333-4333-8333-333333333333",*category=@"44444444-4444-4444-8444-444444444444";
account=[FObject new];account.remObjectID=ident(@"REMCDAccount",source);account.storage=[FStorage new];account.storage.remObjectID=account.remObjectID;list=[FObject new];list.remObjectID=ident(@"REMCDList",listing);list.storage=[FStorage new];list.storage.remObjectID=list.remObjectID;list.storage.accountID=account.remObjectID;list.storage.shouldCategorizeGroceryItems=YES;
graph=@{@"protocol_version":@1,@"source_id":source,@"list_id":listing,@"data_mutated":@NO,@"nodes":@[@{@"id":task,@"parent_id":NSNull.null}],@"ordered_ids":@[task]};sections=@{@"sections":@[]};
NSMutableDictionary *request=[@{@"source_id":source,@"list_id":listing,@"id":category,@"title":@"Synthetic custom grocery category",@"before":@{@"features":features(),@"sections":sections,@"graph":@{@"nodes":@[@{@"id":task,@"parent_id":NSNull.null,@"completed":@NO}],@"ordered_ids":@[task]}}}mutableCopy];
if([mode isEqual:@"ordinary"])list.storage.shouldCategorizeGroceryItems=NO;
if([mode isEqual:@"graph_drift"])graph=@{@"nodes":@[]};if([mode isEqual:@"section_drift"])sections=@{@"sections":@[@{}]};if([mode isEqual:@"extra_field"])request[@"selector"]=@"never";
id response=[NSJSONSerialization JSONObjectWithData:ICloudRemindersCreateGroceryCategory(EncodeFlag(request),^BOOL{gates++;return ![mode isEqual:@"closed"];})options:0 error:NULL];NSData *output=EncodeFlag(@{@"response":response,@"gate_calls":@(gates),@"save_calls":@(saves)});fwrite(output.bytes,1,output.length,stdout);
}}
'''
def fixture():
 s=BASE.replace('@property NSString *entityName;@property NSUUID *uuid;','@property NSString *entityName;@property NSUUID *uuid;+(id)objectIDWithURL:(NSURL*)url;').replace('@implementation FID','@implementation FID\n+(id)objectIDWithURL:(NSURL*)url{FID *v=[FID new];v.entityName=url.host;v.uuid=[[NSUUID alloc]initWithUUIDString:url.lastPathComponent];return v;}',1)
 s=s.replace('@property BOOL shouldCategorizeGroceryItems;','@property BOOL shouldUpdateSectionsOrdering;@property BOOL shouldCategorizeGroceryItems;').replace('v.shouldCategorizeGroceryItems=self.shouldCategorizeGroceryItems;','v.shouldUpdateSectionsOrdering=self.shouldUpdateSectionsOrdering;v.shouldCategorizeGroceryItems=self.shouldCategorizeGroceryItems;').replace('v.shouldCategorizeGroceryItems==self.shouldCategorizeGroceryItems','v.shouldUpdateSectionsOrdering==self.shouldUpdateSectionsOrdering && v.shouldCategorizeGroceryItems==self.shouldCategorizeGroceryItems')
 s=s.replace('-(id)groceryContextChangeItem;','-(id)sectionsContextChangeItem;-(id)groceryContextChangeItem;').replace('@implementation FChange','@implementation FChange\n-(id)sectionsContextChangeItem{return self;}',1)
 a=s.index('@interface FSave');s=s[:a]+SECTION+s[a:];s=s.replace('@property FChange *change;@property FChange *ac;','@property FChange *change;@property FChange *ac;@property FSectionChange *section;\n-(id)addListSectionWithDisplayName:(id)title toListSectionContextChangeItem:(id)context listSectionObjectID:(id)identity;')
 a=s.index('-(id)trackedListChangeItems{');s=s[:a]+METHOD+s[a:];s=s.replace('-(id)trackedListSectionChangeItems{return @{};}','-(id)trackedListSectionChangeItems{FWrap *w=[FWrap new];w.valueWithoutPerformingCopy=self.section;return @{self.section.storage.remObjectID:w};}').replace('*store=[NSObject new]','*store=[FStore new]')
 s+='\nstatic NSData *ICloudRemindersListOrder(NSData *data){return EncodeFlag(graph);}\n';return s

def run():
 source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();prefix=source[:source.index('static BOOL FlagReadInterface(void)')].replace('#import "reminders-model.h"','#import <Foundation/Foundation.h>');support=source[source.index('static id ObjectValue'):source.index('static id SelectedFailure')];tracked=source[source.index('static BOOL TrackedGroupObject'):source.index('NSData *ICloudRemindersCreateRootGroup')];list_tracker=source[source.index('static BOOL LifecycleTrackedList'):source.index('static BOOL LifecycleListPrivate')];graph_support=source[source.index('static BOOL LifecycleGraphMatches'):source.index('static BOOL LifecycleTrackedList')];writer=source[source.index('// Create one custom grocery category;'):]
 for real,fake in(('REMSaveRequest','FSave'),('REMObjectID','FID')):writer=writer.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
 with tempfile.TemporaryDirectory(prefix='native-grocery-category-')as directory:
  p=Path(directory);(p/'test.m').write_text(prefix+support+fixture()+tracked+list_tracker+graph_support+writer+MAIN);result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(p/'test.m'),'-o',str(p/'test')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
  def call(mode):return json.loads(subprocess.run([str(p/'test'),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
  for mode in('valid','closed','save_error'):
   value=call(mode);assert value['gate_calls']==1 and value['save_calls']==(0 if mode=='closed'else 1),value
   if mode=='valid':assert value['response']['result']['saved_locally']is True
   if mode=='save_error':assert value['response']['mutation_dispatched']is True
  for mode in('ordinary','shared','shopping','group','graph_drift','section_drift','extra_field','existing_id','unknown_absence','alias_copy','wrong_wrapper','account_key','account_content','unrelated_reminder','extra_category_key','wrong_category_id','wrong_parent','wrong_title','wrong_date','wrong_version','silent_list_content','extra_list_key'):
   value=call(mode);assert value['gate_calls']==0 and value['save_calls']==0 and value['response']['error'],(mode,value)
 return {'status':'passed','native_stores_opened':False,'exact_new_category_list_refresh_and_unchanged_account':True,'closed_gate_and_unreviewed_effects_refuse':True,'save_error_preserves_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
