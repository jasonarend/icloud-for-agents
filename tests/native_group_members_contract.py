"""Actual scoped group member reader with synthetic physical and custom Smart Lists."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_subtree_contract import fixture_source,shipped_source,ROOT
EXTRA=r'''
static NSArray *smartMembers;
@interface FixtureSmartMembers:NSObject
-(id)fetchCustomSmartListsWithError:(NSError**)error;
@end
@implementation FixtureSmartMembers
-(id)fetchCustomSmartListsWithError:(NSError**)error{return smartMembers;}
@end
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*groupID=@"22222222-2222-4222-8222-222222222222",*listID=@"33333333-3333-4333-8333-333333333333",*smartID=@"44444444-4444-4444-8444-444444444444";
 FixtureAccount *account=[FixtureAccount new];account.remObjectID=Identity(@"REMCDAccount",source);FixtureList *group=[FixtureList new];group.remObjectID=Identity(@"REMCDList",groupID);group.account=account;group.storage=[FixtureListStorage new];group.storage.accountID=account.remObjectID;group.storage.isGroup=YES;moveLists=@[group];fixture=[FixtureReminder new];fixture.list=group;fixture.account=account;
 FixtureList *list=[FixtureList new];list.remObjectID=Identity(@"REMCDList",listID);list.account=account;list.storage=[FixtureListStorage new];list.storage.accountID=account.remObjectID;list.storage.parentListID=group.remObjectID;list.storage.name=@"Synthetic physical child";
 FixtureList *smart=[FixtureList new];smart.remObjectID=Identity(@"REMCDSmartList",smartID);smart.account=account;smart.storage=[FixtureListStorage new];smart.storage.accountID=account.remObjectID;smart.storage.parentListID=group.remObjectID;smart.storage.name=@"Synthetic Smart List child";fixtureGroupChildren=@[list];smartMembers=@[smart];
 NSMutableDictionary *request=[@{@"source_id":source,@"group_id":groupID,@"limit":@100,@"offset":@0}mutableCopy];NSDictionary *(^read)(void)=^NSDictionary*{return [NSJSONSerialization JSONObjectWithData:ICloudRemindersGroupLists([NSJSONSerialization dataWithJSONObject:request options:0 error:NULL]) options:0 error:NULL];};
 NSCAssert([read()[@"lists"]count]==1,@"default physical-only observation unchanged");request[@"include_smart_lists"]=@YES;NSDictionary *both=read();NSCAssert([both[@"lists"]count]==2 && [both[@"custom_smart_lists_included"]isEqual:@YES] && [both[@"backend"]isEqual:@"reminderkit-group-lists-v3"],@"mixed native members included: %@",both);
 NSCAssert([both[@"lists"][0][@"kind"]isEqual:@"list"] && [both[@"lists"][1][@"kind"]isEqual:@"smart_list"],@"native member kinds explicit");
 request[@"limit"]=@1;NSCAssert([read()[@"done"]isEqual:@NO],@"combined list page bounded");request[@"offset"]=@1;NSCAssert([read()[@"lists"][0][@"id"]isEqual:smartID],@"second page includes native smart member");request[@"limit"]=@100;request[@"offset"]=@0;
 request[@"include_smart_lists"]=@1;NSCAssert(read()[@"error"],@"numeric selection refused");request[@"include_smart_lists"]=@YES;
 smart.storage.parentListID=Identity(@"REMCDList",listID);NSCAssert(read()[@"error"],@"foreign group relation refused");smart.storage.parentListID=group.remObjectID;
 smart.storage.accountID=Identity(@"REMCDAccount",listID);NSCAssert(read()[@"error"],@"foreign account refused");smart.storage.accountID=account.remObjectID;
 smart.remObjectID=Identity(@"REMCDSmartList",listID);NSCAssert(read()[@"error"],@"duplicate cross-kind native ID refused");smart.remObjectID=Identity(@"REMCDSmartList",smartID);
 smartMembers=nil;NSCAssert(read()[@"error"],@"unavailable provider is not empty");smartMembers=@[];fixtureGroupChildren=@[];NSCAssert([read()[@"lists"]isEqual:@[]] && [read()[@"done"]isEqual:@YES],@"observed empty physical and custom smart collections");
}}
'''
def run():
    fixture=fixture_source().replace('@interface FixtureList:NSObject',EXTRA+'\n@interface FixtureList:NSObject\n-(id)sublistContext;').replace('@implementation FixtureList\n','@implementation FixtureList\n-(id)sublistContext{return [FixtureSmartMembers new];}\n')
    with tempfile.TemporaryDirectory(prefix='native-group-members-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(shipped_source()+'\n'+fixture+'\n'+MAIN)
        r=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr
        r=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert r.returncode==0,r.stderr
    return {'status':'passed','apple_store_opened':False,'mixed_physical_smart_group_members_verified':True,'foreign_duplicate_unavailable_and_unbounded_refused':True}
if __name__=='__main__':print(json.dumps(run()))
