"""Execute the shipped empty-list flag guard with adversarial in-memory objects."""
import json,subprocess,tempfile
from pathlib import Path
from native_template_create_contract import FIXTURE as TEMPLATE_FIXTURE,ROOT
FIXTURE=r'''
static NSString *mode;static int saves=0,gates=0;static NSDictionary *graph,*sections;
@interface FID:NSObject<NSCopying>
@property NSString *entityName;@property NSUUID *uuid;
@end
@implementation FID
-(id)copyWithZone:(NSZone*)zone{return self;}
@end
static FID *ident(NSString *entity,NSString *uuid){FID *v=[FID new];v.entityName=entity;v.uuid=[[NSUUID alloc]initWithUUIDString:uuid];return v;}
@interface FStorage:NSObject<NSCopying>
@property FID *remObjectID;@property FID *accountID;@property NSString *name;@property BOOL shouldCategorizeGroceryItems;@property BOOL shouldSupportGroceryShoppingSession;
@end
@implementation FStorage
-(id)copyWithZone:(NSZone*)zone{if([mode isEqual:@"alias_copy"])return self;FStorage *v=[FStorage new];v.remObjectID=self.remObjectID;v.accountID=self.accountID;v.name=self.name;v.shouldCategorizeGroceryItems=self.shouldCategorizeGroceryItems;v.shouldSupportGroceryShoppingSession=self.shouldSupportGroceryShoppingSession;return v;}
-(BOOL)isEqual:(id)value{if(![value isKindOfClass:FStorage.class])return NO;FStorage *v=value;return v.remObjectID==self.remObjectID && v.accountID==self.accountID && v.name==self.name && v.shouldCategorizeGroceryItems==self.shouldCategorizeGroceryItems && v.shouldSupportGroceryShoppingSession==self.shouldSupportGroceryShoppingSession;}
@end
@interface FObject:NSObject
@property FID *remObjectID;@property FStorage *storage;
@end
@implementation FObject @end
@class FCtx;
@interface FChange:NSObject
@property FStorage *storage;@property NSMutableSet *changedKeys;
-(id)groceryContextChangeItem;
@end
@interface FCtx:NSObject
@property FChange *change;
-(void)setShouldCategorizeGroceryItems:(BOOL)value;
@end
@implementation FCtx
-(void)setShouldCategorizeGroceryItems:(BOOL)value{self.change.storage.shouldCategorizeGroceryItems=value;[self.change.changedKeys addObject:@"shouldCategorizeGroceryItems"];if([mode isEqual:@"extra_key"])[self.change.changedKeys addObject:@"name"];if([mode isEqual:@"silent_content"])self.change.storage.name=@"Unreviewed";if([mode isEqual:@"silent_session"])self.change.storage.shouldSupportGroceryShoppingSession=YES;}
@end
@implementation FChange
-(id)groceryContextChangeItem{FCtx *v=[FCtx new];v.change=self;return v;}
@end
@interface FWrap:NSObject
@property id valueWithoutPerformingCopy;
@end
@implementation FWrap @end
static FObject *account,*list;
@interface FSave:NSObject
@property FChange *change;@property FChange *ac;
-(id)initWithStore:(id)store;-(id)updateList:(id)value;-(BOOL)saveSynchronouslyWithError:(NSError**)error;
-(id)trackedAccountChangeItems;-(id)trackedListChangeItems;-(id)trackedListSectionChangeItems;-(id)trackedReminderChangeItems;-(id)trackedSmartListChangeItems;-(id)trackedSmartListSectionChangeItems;-(id)trackedTemplateChangeItems;-(id)trackedTemplateSectionChangeItems;
@end
@implementation FSave
-(id)initWithStore:(id)store{return [super init];}
-(id)updateList:(FObject*)value{self.change=[FChange new];self.change.storage=[value.storage copy];self.change.changedKeys=[NSMutableSet set];self.ac=[FChange new];self.ac.storage=[account.storage copy];self.ac.changedKeys=[NSMutableSet set];if([mode isEqual:@"account_key"])[self.ac.changedKeys addObject:@"name"];if([mode isEqual:@"account_content"])self.ac.storage.name=@"Changed account";return self.change;}
-(id)trackedListChangeItems{FWrap *v=[FWrap new];v.valueWithoutPerformingCopy=[mode isEqual:@"wrong_wrapper"]?[NSObject new]:self.change;return @{list.remObjectID:v};}
-(id)trackedAccountChangeItems{FWrap *v=[FWrap new];v.valueWithoutPerformingCopy=self.ac;return @{account.remObjectID:v};}
-(id)trackedListSectionChangeItems{return @{};}-(id)trackedReminderChangeItems{return [mode isEqual:@"unrelated_reminder"]?@{@"unreviewed":@1}:@{};}-(id)trackedSmartListChangeItems{return @{};}-(id)trackedSmartListSectionChangeItems{return @{};}-(id)trackedTemplateChangeItems{return @{};}-(id)trackedTemplateSectionChangeItems{return @{};}
-(BOOL)saveSynchronouslyWithError:(NSError**)error{saves++;if([mode isEqual:@"save_error"]){*error=[NSError errorWithDomain:@"synthetic"code:1 userInfo:nil];return NO;}return YES;}
@end
static NSDictionary *features(void){return @{@"source_id":account.remObjectID.uuid.UUIDString,@"list_id":list.remObjectID.uuid.UUIDString,@"grocery_categorization":@(list.storage.shouldCategorizeGroceryItems),@"grocery_shopping_session":@([mode isEqual:@"shopping"]),@"is_group":@([mode isEqual:@"group"]),@"shared":@([mode isEqual:@"shared"]),@"shared_to_me":@NO};}
static NSData *ICloudRemindersReadListFeatures(NSData *data){return EncodeFlag(features());}
static NSData *ICloudRemindersListHierarchy(NSData *data){return EncodeFlag(graph);}
static NSData *ICloudRemindersListSections(NSData *data){return EncodeFlag(sections);}
static id SelectedGroupAccount(NSString *source,id __strong *store,NSDictionary **failure){if(![source isEqual:account.remObjectID.uuid.UUIDString]){*failure=FlagFailure(@"foreign");return nil;}*store=[NSObject new];return account;}
static id ScopedGroupList(id store,NSString *source,NSString *listing){return [listing isEqual:list.remObjectID.uuid.UUIDString]?list:nil;}
'''
MAIN=r'''
int main(int argc,const char**argv){@autoreleasepool{
mode=[NSString stringWithUTF8String:argv[1]];NSString *source=@"11111111-1111-4111-8111-111111111111",*listing=@"22222222-2222-4222-8222-222222222222";account=[FObject new];account.remObjectID=ident(@"REMCDAccount",source);account.storage=[FStorage new];account.storage.remObjectID=account.remObjectID;list=[FObject new];list.remObjectID=ident(@"REMCDList",listing);list.storage=[FStorage new];list.storage.remObjectID=list.remObjectID;list.storage.accountID=account.remObjectID;
graph=@{@"nodes":@[]};sections=@{@"sections":@[]};NSMutableDictionary *request=[@{@"source_id":source,@"list_id":listing,@"enabled":@YES,@"before":@{@"features":features(),@"graph":graph,@"sections":sections}}mutableCopy];
if([mode isEqual:@"populated"])graph=@{@"nodes":@[@{}]};if([mode isEqual:@"sectioned"])sections=@{@"sections":@[@{}]};if([mode isEqual:@"foreign"])request[@"source_id"]=listing;if([mode isEqual:@"number_bool"])request[@"enabled"]=@1;if([mode isEqual:@"extra_field"])request[@"selector"]=@"never";
id value=[NSJSONSerialization JSONObjectWithData:ICloudRemindersSetGroceryCategorization(EncodeFlag(request),^BOOL{gates++;return ![mode isEqual:@"closed"];})options:0 error:NULL];NSData *output=EncodeFlag(@{@"response":value,@"gate_calls":@(gates),@"save_calls":@(saves)});fwrite(output.bytes,1,output.length,stdout);
}}
'''
def run():
 source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();prefix=source[:source.index('static BOOL FlagReadInterface(void)')].replace('#import "reminders-model.h"','#import <Foundation/Foundation.h>');support=source[source.index('static id ObjectValue'):source.index('static id SelectedFailure')];tracked=source[source.index('static BOOL TrackedGroupObject'):source.index('NSData *ICloudRemindersCreateRootGroup')];list_tracker=source[source.index('static BOOL LifecycleTrackedList'):source.index('static BOOL LifecycleListPrivate')];writer=source[source.index('// Change only a known categorization flag;'):].replace('NSClassFromString(@"REMSaveRequest")','NSClassFromString(@"FSave")')
 with tempfile.TemporaryDirectory(prefix='native-grocery-mode-')as directory:
  p=Path(directory);(p/'test.m').write_text(prefix+support+FIXTURE+tracked+list_tracker+writer+MAIN);result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(p/'test.m'),'-o',str(p/'test')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
  def call(mode):return json.loads(subprocess.run([str(p/'test'),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
  for mode in('valid','closed','save_error'):
   value=call(mode);assert value['gate_calls']==1 and value['save_calls']==(0 if mode=='closed'else 1),value
   if mode=='valid':assert value['response']['result']['saved_locally']is True
   if mode=='save_error':assert value['response']['mutation_dispatched']is True
  for mode in('extra_key','silent_content','silent_session','account_key','account_content','wrong_wrapper','unrelated_reminder','alias_copy','populated','sectioned','foreign','number_bool','extra_field','shopping','group','shared'):
   value=call(mode);assert value['gate_calls']==0 and value['save_calls']==0 and value['response']['error'],(mode,value)
 return {'status':'passed','native_stores_opened':False,'only_flag_changed_full_storage_account_guard':True,'closed_gate_and_adversarial_changes_refused':True,'failed_save_uncertainty_preserved':True}
if __name__=='__main__':print(json.dumps(run()))
