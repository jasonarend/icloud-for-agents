"""Execute the shipped creation guard over isolated native constructor fixtures."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_groups_contract import groups_fixture,shipped_source,ROOT

SMART = r'''
static NSString *mode;
static NSData *capturedFilter;
@interface FixtureManual:NSObject
@property FixtureID *objectID;@property NSString *listID;@property short listType;
@property NSDate *modifiedDate;@property NSArray *topLevelElementIDs;
@property NSDictionary *secondaryLevelElementIDsByTopLevelElementID;
@property id uncommitedElementsAccountID;
@end
@implementation FixtureManual @end
@interface FixtureSmartStorage:NSObject
@property FixtureID *objectID;@property FixtureID *accountID;@property FixtureID *parentAccountID;
@property NSString *name;@property NSData *filterData;@property NSString *smartListType;
@property FixtureManual *manualOrdering;@property long long minimumSupportedVersion;
@property NSNumber *shouldUpdateSectionsOrdering;@property NSNumber *showingLargeAttachments;
@property NSSet *sectionIDsToUndelete;
@end
@implementation FixtureSmartStorage
-(id)remObjectID{return self.objectID;}
@end
@interface FixtureSmartChange:NSObject
@property FixtureSmartStorage *storage;@property NSMutableSet *changedKeys;
@end
@implementation FixtureSmartChange
-(void)setMinimumSupportedVersion:(long long)value{self.storage.minimumSupportedVersion=[mode isEqual:@"wrong_version"]?0:value;}
-(void)setFilterData:(id)value{self.storage.filterData=[mode isEqual:@"filter_drift"]?[NSData data]:value;capturedFilter=self.storage.filterData;[self.changedKeys addObject:@"filterData"];}
@end
@interface FixtureSmartView:NSObject
-(id)initWithStore:(id)store;-(id)fetchCustomSmartListsInAccount:(id)account error:(NSError**)error;
@end
static NSArray *views;
@implementation FixtureSmartView
-(id)initWithStore:(id)store{return [super init];}
-(id)fetchCustomSmartListsInAccount:(id)account error:(NSError**)error{return views;}
@end
'''
SAVE = r'''
@implementation FixtureSave
-(id)initWithStore:(id)store{return [super init];}
-(id)updateAccount:(id)account{FixtureAccountChange *c=[FixtureAccountChange new];c.storage=[FixtureAccountStorage new];c.storage.listIDsMergeableOrdering=[FixtureMergeOrder new];c.storage.listIDsMergeableOrdering.orderedSet=groupOrder;c.changedKeys=[NSSet set];self.groupAccount=c;return c;}
-(id)addCustomSmartListWithName:(NSString*)name toAccountChangeItem:(FixtureAccountChange*)account smartListObjectID:(FixtureID*)identity{
 FixtureSmartChange *c=[FixtureSmartChange new];FixtureSmartStorage *s=[FixtureSmartStorage new];c.storage=s;
 c.changedKeys=[NSMutableSet setWithArray:@[@"accountID",@"manualOrdering",@"minimumSupportedVersion",@"name",@"objectID",@"parentAccountID",@"sectionIDsToUndelete",@"shouldUpdateSectionsOrdering",@"showingLargeAttachments",@"smartListType"]];
 if([mode isEqual:@"extra_key"])[c.changedKeys addObject:@"notes"];
 s.objectID=identity;s.accountID=fixture.account.remObjectID;s.parentAccountID=fixture.account.remObjectID;s.name=name;s.smartListType=@"com.apple.reminders.smartlist.custom";s.minimumSupportedVersion=0;s.shouldUpdateSectionsOrdering=@NO;s.showingLargeAttachments=@NO;s.sectionIDsToUndelete=[NSSet set];
 s.manualOrdering=[FixtureManual new];s.manualOrdering.objectID=Identity(@"REMCDManualSortHint_v1",@"66666666-6666-4666-8666-666666666666");s.manualOrdering.listID=identity.uuid.UUIDString;s.manualOrdering.listType=2;s.manualOrdering.modifiedDate=[NSDate date];s.manualOrdering.topLevelElementIDs=@[];s.manualOrdering.secondaryLevelElementIDsByTopLevelElementID=@{};
 if([mode isEqual:@"wrong_manual_id"])s.manualOrdering.listID=fixture.account.remObjectID.uuid.UUIDString;
 if([mode isEqual:@"wrong_manual_type"])s.manualOrdering.listType=1;
 if([mode isEqual:@"manual_content"])s.manualOrdering.topLevelElementIDs=@[fixture.account.remObjectID];
 if([mode isEqual:@"manual_secondary"])s.manualOrdering.secondaryLevelElementIDsByTopLevelElementID=@{@"unreviewed":@1};
 if([mode isEqual:@"manual_account"])s.manualOrdering.uncommitedElementsAccountID=fixture.account.remObjectID;
 if([mode isEqual:@"wrong_type"])s.smartListType=@"built-in";
 if([mode isEqual:@"wrong_parent"])s.parentAccountID=identity;
 if([mode isEqual:@"wrong_default"])s.showingLargeAttachments=@YES;
 if([mode isEqual:@"wrong_title"])s.name=@"Unreviewed title";
 if([mode isEqual:@"undelete"])s.sectionIDsToUndelete=[NSSet setWithObject:identity];
 NSMutableArray *order=[groupOrder.array mutableCopy];[order addObject:identity.uuid.UUIDString];if([mode isEqual:@"reorder"])[order exchangeObjectAtIndex:0 withObjectAtIndex:1];account.storage.listIDsMergeableOrdering.orderedSet=[NSOrderedSet orderedSetWithArray:order];account.changedKeys=[NSSet setWithArray:[mode isEqual:@"account_key"]?@[@"listIDsMergeableOrdering",@"name"]:@[@"listIDsMergeableOrdering"]];self.createdSmart=c;return c;
}
-(id)trackedAccountChangeItems{FixtureTracked *w=[FixtureTracked new];w.valueWithoutPerformingCopy=[mode isEqual:@"wrong_wrapper"]?[NSObject new]:self.groupAccount;return @{fixture.account.remObjectID:w};}
-(id)trackedSmartListChangeItems{FixtureTracked *w=[FixtureTracked new];w.valueWithoutPerformingCopy=self.createdSmart;return @{((FixtureSmartChange*)self.createdSmart).storage.objectID:w};}
-(id)trackedReminderChangeItems{return [mode isEqual:@"unrelated_reminder"]?@{@"unreviewed":@YES}:@{};}
-(id)trackedListChangeItems{return @{};}-(id)trackedListSectionChangeItems{return @{};}
-(id)trackedSmartListSectionChangeItems{return @{};}-(id)trackedTemplateChangeItems{return @{};}-(id)trackedTemplateSectionChangeItems{return @{};}
-(BOOL)saveSynchronouslyWithError:(NSError**)error{flagSaves++;if([mode isEqual:@"save_error"]){*error=[NSError errorWithDomain:@"synthetic"code:1 userInfo:nil];return NO;}return YES;}
@end
'''
MAIN = r'''
int main(int argc,const char **argv){@autoreleasepool{
 mode=[NSString stringWithUTF8String:argv[1]];NSString *source=@"11111111-1111-4111-8111-111111111111",*listID=@"22222222-2222-4222-8222-222222222222",*identity=@"33333333-3333-4333-8333-333333333333";
 fixture=[FixtureReminder new];fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);groupAccounts=@[fixture.account];groupValues=@[];views=@[];groupOrder=[NSOrderedSet orderedSetWithObject:listID];
 FixtureList *list=[FixtureList new];list.remObjectID=Identity(@"REMCDList",listID);list.account=fixture.account;list.storage=[FixtureListStorage new];list.storage.accountID=fixture.account.remObjectID;moveLists=@[list];
 NSDictionary *roots=[NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupInventory(EncodeFlag(@{@"source_id":source}))options:0 error:NULL];
 NSDictionary *scope=@{@"list":@{@"id":listID,@"source_id":source},@"role":@{@"list_id":listID,@"source_id":source,@"grocery_categorization":@NO,@"grocery_shopping_session":@NO,@"is_group":@NO,@"shared":@NO,@"shared_to_me":@NO}};
 NSMutableDictionary *request=[@{@"source_id":source,@"id":identity,@"title":@"Synthetic created view",@"list_ids":@[listID],@"before":@{@"roots":roots,@"views":@[],@"scopes":@[scope]}}mutableCopy];
 if([mode isEqual:@"shared"])list.isShared=YES;if([mode isEqual:@"grocery"])list.storage.shouldCategorizeGroceryItems=YES;
 if([mode isEqual:@"foreign_reference"])list.storage.accountID=Identity(@"REMCDAccount",identity);
 if([mode isEqual:@"root_drift"])groupOrder=[NSOrderedSet orderedSet];
 if([mode isEqual:@"duplicate_ids"])request[@"list_ids"]=@[listID,listID];
 if([mode isEqual:@"missing_scope"]){NSMutableDictionary *b=[request[@"before"]mutableCopy];b[@"scopes"]=@[];request[@"before"]=b;}
 if([mode isEqual:@"empty_ids"])request[@"list_ids"]=@[];
 if([mode isEqual:@"extra_field"])request[@"raw_filter"]=@"{}";
 if([mode isEqual:@"control_title"])request[@"title"]=@"Bad\nTitle";
 if([mode isEqual:@"today"] || [mode isEqual:@"overdue"])request[@"date_filter"]=@{@"mode":@"today",@"include_overdue":@([mode isEqual:@"overdue"])};
 if([mode isEqual:@"date_number"])request[@"date_filter"]=@{@"mode":@"today",@"include_overdue":@1};
 if([mode isEqual:@"date_null"])request[@"date_filter"]=NSNull.null;
 if([mode isEqual:@"date_unknown"])request[@"date_filter"]=@{@"mode":@"tomorrow",@"include_overdue":@NO};
 if([mode isEqual:@"date_extra"])request[@"date_filter"]=@{@"mode":@"today",@"include_overdue":@NO,@"extra":@YES};
 __block int gates=0;id response=[NSJSONSerialization JSONObjectWithData:ICloudRemindersCreateSmartList(EncodeFlag(request),^BOOL{gates++;return ![mode isEqual:@"closed"];})options:0 error:NULL];
 id filter=capturedFilter?[NSJSONSerialization JSONObjectWithData:capturedFilter options:0 error:NULL]:@{};
 NSData *output=EncodeFlag(@{@"response":response,@"filter":filter?:@{},@"gate_calls":@(gates),@"save_calls":@(flagSaves)});fwrite(output.bytes,1,output.length,stdout);return 0;
}}
'''


def run():
    fixture=groups_fixture()
    fixture=fixture.replace('@property FixtureGroupChange *createdGroup;','@property FixtureGroupChange *createdGroup;\n@property id createdSmart;\n-(id)addCustomSmartListWithName:(id)name toAccountChangeItem:(id)account smartListObjectID:(id)identity;')
    start=fixture.index('@implementation FixtureSave');end=fixture.index('\n@end',start)+len('\n@end')
    fixture=fixture[:start]+SMART+SAVE+fixture[end:]
    source=shipped_source().replace('NSClassFromString(@"REMSmartListsDataView")','NSClassFromString(@"FixtureSmartView")')
    with tempfile.TemporaryDirectory(prefix='native-smart-create-') as directory:
        path=Path(directory);(path/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text())
        (path/'test.m').write_text(source+'\n'+fixture+'\n'+MAIN)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(path/'test.m'),'-o',str(path/'contract')],capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stderr
        def call(mode):return json.loads(subprocess.run([str(path/'contract'),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
        closed=call('closed');assert closed['gate_calls']==1 and closed['save_calls']==0 and closed['response']['error'],closed
        opened=call('valid');assert opened['gate_calls']==1 and opened['save_calls']==1 and opened['response']['result']['saved_locally'] is True,opened
        for mode,overdue in (('today',False),('overdue',True)):
            value=call(mode);assert value['gate_calls']==1 and value['save_calls']==1 and value['filter']['date']=={'today':overdue},value
        failed=call('save_error');assert failed['save_calls']==1 and failed['response']['mutation_dispatched'] is True
        for mode in ('date_number','date_null','date_unknown','date_extra','shared','grocery','foreign_reference','root_drift','duplicate_ids','missing_scope','empty_ids','extra_field','control_title','extra_key','filter_drift','wrong_version','wrong_manual_id','wrong_manual_type','manual_content','manual_secondary','manual_account','wrong_type','wrong_parent','wrong_default','wrong_title','undelete','reorder','account_key','wrong_wrapper','unrelated_reminder'):
            value=call(mode);assert value['gate_calls']==0 and value['save_calls']==0 and value['response']['error'],(mode,value)
    return {'status':'passed','apple_store_opened':False,'exact_preassigned_new_view_and_root_append':True,'empty_manual_order_verified':True,'closed_gate_and_unreviewed_side_effects_refused':True,'failed_save_preserves_uncertainty':True}


if __name__=='__main__':print(json.dumps(run()))
