"""Run exact category naming against adversarial in-memory native storage."""
import json,subprocess,tempfile
from pathlib import Path
from native_grocery_categories_contract import fixture as category_fixture,MAIN as CATEGORY_MAIN,ROOT

def fixture():
 s=category_fixture().replace('@interface FSectionStorage:NSObject','@interface FSectionStorage:NSObject<NSCopying>')
 s=s.replace('@implementation FSectionStorage',r'''
@implementation FSectionStorage
-(id)copyWithZone:(NSZone*)zone{if([mode isEqual:@"section_alias"])return self;FSectionStorage *v=[FSectionStorage new];v.remObjectID=self.remObjectID;v.accountID=self.accountID;v.parentID=self.parentID;v.displayName=self.displayName;v.creationDate=self.creationDate;v.minimumSupportedVersion=self.minimumSupportedVersion;return v;}
-(BOOL)isEqual:(id)other{if(![other isKindOfClass:FSectionStorage.class])return NO;FSectionStorage *v=other;return v.remObjectID==self.remObjectID && v.accountID==self.accountID && v.parentID==self.parentID && [v.displayName isEqual:self.displayName] && v.creationDate==self.creationDate && v.minimumSupportedVersion==self.minimumSupportedVersion;}
''')
 s=s.replace('@implementation FSectionChange @end',r'''
@implementation FSectionChange
-(void)setDisplayName:(NSString*)title{self.storage.displayName=[mode isEqual:@"wrong_title"]?@"Unreviewed":title;self.changedKeys=[NSMutableSet setWithObject:@"displayName"];if([mode isEqual:@"extra_category_key"])[self.changedKeys addObject:@"unknown"];if([mode isEqual:@"silent_category"] )self.storage.minimumSupportedVersion++;}
@end
static FSectionChange *existingCategory;
''')
 a=s.index('-(id)fetchListSectionWithObjectID:');a=s.index('-(id)fetchListSectionWithObjectID:',a+1);b=s.index('\n@end',a);s=s[:a]+r'''-(id)fetchListSectionWithObjectID:(id)identity error:(NSError**)error{if([mode isEqual:@"missing"])return nil;return existingCategory;}'''+s[b:]
 s=s.replace('@property FSectionChange *section;','@property FSectionChange *section;\n-(id)updateListSection:(id)value;')
 a=s.index('-(id)trackedListChangeItems{');s=s[:a]+r'''
-(id)updateListSection:(FSectionChange*)value{[self updateList:list];self.section=[FSectionChange new];self.section.storage=[value.storage copy];self.section.changedKeys=[NSMutableSet set];if([mode isEqual:@"silent_list_content"])self.change.storage.name=@"Unreviewed";if([mode isEqual:@"extra_list_key"])[self.change.changedKeys addObject:@"name"];return self.section;}
'''+s[a:]
 return s

def main():
 s=CATEGORY_MAIN.replace('sections=@{@"sections":@[]};',r'''existingCategory=[FSectionChange new];existingCategory.storage=[FSectionStorage new];existingCategory.storage.remObjectID=ident(@"REMCDListSection",category);existingCategory.storage.accountID=account.remObjectID;existingCategory.storage.parentID=list.remObjectID;existingCategory.storage.displayName=@"Synthetic old category";existingCategory.storage.creationDate=[NSDate date];sections=@{@"sections":@[@{@"id":category,@"title":@"Synthetic old category"}]};''')
 s=s.replace('ICloudRemindersCreateGroceryCategory','ICloudRemindersRenameGroceryCategory')
 marker='id response=[NSJSONSerialization';i=s.index(marker);s=s[:i]+r'''
if([mode isEqual:@"wrong_category_id"])existingCategory.storage.remObjectID=list.remObjectID;
if([mode isEqual:@"wrong_parent"])existingCategory.storage.parentID=account.remObjectID;
if([mode isEqual:@"foreign_account"])existingCategory.storage.accountID=list.remObjectID;
if([mode isEqual:@"noop"])request[@"title"]=@"Synthetic old category";
'''+s[i:]
 return s

def run():
 source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();prefix=source[:source.index('static BOOL FlagReadInterface(void)')].replace('#import "reminders-model.h"','#import <Foundation/Foundation.h>');support=source[source.index('static id ObjectValue'):source.index('static id SelectedFailure')];tracked=source[source.index('static BOOL TrackedGroupObject'):source.index('NSData *ICloudRemindersCreateRootGroup')];list_tracker=source[source.index('static BOOL LifecycleTrackedList'):source.index('static BOOL LifecycleListPrivate')];graph_support=source[source.index('static BOOL LifecycleGraphMatches'):source.index('static BOOL LifecycleTrackedList')];start=source.index('NSData *ICloudRemindersRenameGroceryCategory(');writer=source[start:source.index('\n}\n',start)+3]
 for real,fake in(('REMSaveRequest','FSave'),('REMObjectID','FID')):writer=writer.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
 with tempfile.TemporaryDirectory(prefix='native-grocery-category-rename-')as directory:
  p=Path(directory);(p/'test.m').write_text(prefix+support+fixture()+tracked+list_tracker+graph_support+writer+main());r=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(p/'test.m'),'-o',str(p/'test')],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr
  def call(mode):return json.loads(subprocess.run([str(p/'test'),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
  for mode in('valid','closed','save_error'):
   value=call(mode);assert value['gate_calls']==1 and value['save_calls']==(0 if mode=='closed'else 1),(mode,value)
   if mode=='valid':assert value['response']['result']['saved_locally']is True
   if mode=='save_error':assert value['response']['mutation_dispatched']is True
  for mode in('ordinary','shared','shopping','group','graph_drift','section_drift','extra_field','missing','alias_copy','section_alias','wrong_wrapper','account_key','account_content','unrelated_reminder','extra_category_key','wrong_category_id','wrong_parent','foreign_account','wrong_title','silent_category','silent_list_content','extra_list_key','noop'):
   value=call(mode);assert value['gate_calls']==0 and value['save_calls']==0 and value['response']['error'],(mode,value)
 return {'status':'passed','native_stores_opened':False,'category_name_only_full_storage_and_exact_trackers_verified':True,'closed_gate_unreviewed_effects_scope_and_noop_refuse':True}
if __name__=='__main__':print(json.dumps(run()))
