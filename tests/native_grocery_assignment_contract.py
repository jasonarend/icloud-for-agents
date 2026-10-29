"""Exercise the fixed grocery membership delta with synthetic native storage."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_sections_contract import FIXTURE as BASE,MAIN as BASE_MAIN
ROOT=Path(__file__).resolve().parents[1]
def fixture():
 s=BASE.replace('static int saves=0;','static int saves=0,roleReads=0;static BOOL grocery=YES,shopping=NO,group=NO,silentList=NO,silentAccount=NO,aliasCopy=NO,roleDrift=NO;')
 s=s.replace('@interface FixtureObject',r'''
@interface FStorage:NSObject<NSCopying>
@property NSString *title;
@property id unsavedMembershipsOfRemindersInSections;
@end
@implementation FStorage
-(id)copyWithZone:(NSZone*)zone{if(aliasCopy)return self;FStorage *value=[FStorage new];value.title=self.title;value.unsavedMembershipsOfRemindersInSections=self.unsavedMembershipsOfRemindersInSections;return value;}
-(BOOL)isEqual:(id)other{return [other isKindOfClass:FStorage.class] && [self.title isEqual:[other title]] && (self.unsavedMembershipsOfRemindersInSections==[other unsavedMembershipsOfRemindersInSections] || [self.unsavedMembershipsOfRemindersInSections isEqual:[other unsavedMembershipsOfRemindersInSections]]);}
@end
@interface FixtureObject
''')
 s=s.replace('@property id list,parentReminder;','@property id list,parentReminder,account,storage;')
 s=s.replace('static BOOL LifecycleListPrivate(id list,NSString *source,NSString *identifier){return !shared && list==listObject;}','static id ScopedGroupList(id store,id source,id identifier){return listObject;}\nstatic NSData *ICloudRemindersReadListFeatures(NSData *data){roleReads++;return EncodeFlag(@{@"grocery_categorization":@(grocery),@"grocery_shopping_session":@(shopping),@"is_group":@(group),@"shared":@(shared || (roleDrift && roleReads>1)),@"shared_to_me":@NO});}')
 s=s.replace('@property NSSet *changedKeys;','@property NSSet *changedKeys;\n@property id storage;')
 s=s.replace('self.change.sectionsContextChangeItem=self.change;return self.change;','self.change.sectionsContextChangeItem=self.change;self.change.storage=[ObjectValue(list,@"storage")copy];if(silentList)[self.change.storage setValue:@"unreviewed"forKey:@"title"];if(silentAccount)[ObjectValue(accountChange,@"storage")setValue:@"unreviewed"forKey:@"title"];return self.change;')
 s=s.replace("_unsavedMembershipsOfRemindersInSections=value;","_unsavedMembershipsOfRemindersInSections=value;[self.storage setUnsavedMembershipsOfRemindersInSections:value];")
 return s

def main():
 s=BASE_MAIN.replace('int main(){','int main(int argc,char **argv){').replace('listObject=[NSObject new];','FixtureObject *listing=[FixtureObject new];listing.identifier=list;FStorage *ls=[FStorage new];ls.title=@"Synthetic grocery";listing.storage=ls;FixtureObject *accountObject=[FixtureObject new];FStorage *as=[FStorage new];as.title=@"Synthetic account";accountObject.storage=as;listing.account=accountObject;listObject=listing;')
 s=s.replace('accountChange=account;','account.storage=accountObject.storage;accountChange=account;')
 start=s.index(' NSCAssert([call(NO)');s=s[:start]+r'''
 NSString *mode=argc>1?[NSString stringWithUTF8String:argv[1]]:@"valid";
 if([mode isEqual:@"ordinary"])grocery=NO;if([mode isEqual:@"shopping"])shopping=YES;if([mode isEqual:@"group"])group=YES;if([mode isEqual:@"shared"])shared=YES;
 if([mode isEqual:@"extra_keys"])extraKeys=YES;if([mode isEqual:@"unrelated_task"])extraTask=YES;if([mode isEqual:@"wrong_wrapper"])badWrapper=YES;
 if([mode isEqual:@"silent_list"])silentList=YES;if([mode isEqual:@"silent_account"])silentAccount=YES;if([mode isEqual:@"role_drift"])roleDrift=YES;
 if([mode isEqual:@"alias_copy"])aliasCopy=YES;
 if([mode isEqual:@"nested"])graph=@{@"nodes":@[@{@"id":identifier,@"parent_id":a}],@"ordered_ids":@[identifier]};
 if([mode isEqual:@"foreign_category"])r[@"section_id"]=list;if([mode isEqual:@"noop"])r[@"section_id"]=a;if([mode isEqual:@"future"])r[@"previous_modified_on"]=@1e20;
 if([mode isEqual:@"stale_category"])r[@"before_section"]=@{};
 if([mode isEqual:@"remove"])r[@"section_id"]=NSNull.null;if([mode isEqual:@"save_error"])saveFails=YES;
 NSDictionary *response=call(![mode isEqual:@"closed"]);NSData *output=EncodeFlag(@{@"response":response,@"gate_calls":@(gates),@"save_calls":@(saves)});puts([[NSString alloc]initWithData:output encoding:NSUTF8StringEncoding].UTF8String);
}}
'''
 return s.replace('ICloudRemindersSetSection','ICloudRemindersSetGroceryCategory')
def run():
 source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text();start=source.index('NSData *ICloudRemindersSetGroceryCategory(');writer=source[start:source.index('\n}\n',start)+3];helper=source[source.index('static NSDictionary *MutationFailure('):source.index('static NSString *Signature(')]
 with tempfile.TemporaryDirectory(prefix='native-grocery-assignment-')as directory:
  p=Path(directory);(p/'test.m').write_text(fixture()+helper+writer+main());r=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(p/'test.m'),'-o',str(p/'test')],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr
  def call(mode):return json.loads(subprocess.run([str(p/'test'),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
  for mode in('valid','remove','closed','save_error'):
   value=call(mode);assert value['gate_calls']==1 and value['save_calls']==(0 if mode=='closed'else 1),(mode,value)
   if mode in('valid','remove'):assert value['response']['result']['saved_locally']is True
   if mode=='save_error':assert value['response']['mutation_dispatched']is True
  for mode in('ordinary','shopping','group','shared','extra_keys','unrelated_task','wrong_wrapper','silent_list','silent_account','role_drift','alias_copy','nested','foreign_category','noop','future','stale_category'):
   value=call(mode);assert value['gate_calls']==0 and value['save_calls']==0 and value['response']['error'],(mode,value)
 return {'status':'passed','native_stores_opened':False,'exact_grocery_membership_delta_full_storage_and_gate_verified':True,'unreviewed_effects_and_scope_changes_refused':True}
if __name__=='__main__':print(json.dumps(run()))
