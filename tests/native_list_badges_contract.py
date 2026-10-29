"""Actual fixed badge writer against synthetic native storage, without Apple data."""
import json,subprocess,tempfile
from pathlib import Path
from native_reminder_subtree_contract import fixture_source,shipped_source,ROOT
EXTRA=r'''
static BOOL extraBadgeEffect=NO,wrongBadge=NO;
@interface FixtureBadge:NSObject
@property NSString *rawValue;
@property NSString *emoji;
@property NSString *emblem;
-(id)initWithRawValue:(id)value;
-(id)initWithEmoji:(id)value;
-(id)initWithEmblem:(id)value;
-(BOOL)_isSingleCharacterEmoji:(id)value;
@end
@implementation FixtureBadge
-(id)initWithRawValue:(id)value{self=[super init];self.rawValue=value?:@"null emoji";self.emoji=value?nil:@"(null)";return self;}
-(id)initWithEmoji:(id)value{self=[super init];self.rawValue=value;self.emoji=value;return self;}
-(id)initWithEmblem:(id)value{self=[super init];self.rawValue=value;self.emblem=value;return self;}
-(BOOL)_isSingleCharacterEmoji:(id)value{return [value isEqual:@"🧪"];}
@end
@interface FixtureImage:NSObject
+(id)imageWithSystemSymbolName:(id)value accessibilityDescription:(id)description;
@end
@implementation FixtureImage
+(id)imageWithSystemSymbolName:(id)value accessibilityDescription:(id)description{return [value isEqual:@"star.fill"]?[NSObject new]:nil;}
@end
@interface FixtureAppearance:NSObject
@property FixtureBadge *badge;
@property NSSet *changedKeys;
-(id)badgeEmblem;
@end
@implementation FixtureAppearance
-(id)badgeEmblem{return self.badge.rawValue;}
-(void)setBadge:(FixtureBadge*)badge{_badge=wrongBadge?[[FixtureBadge alloc]initWithEmblem:@"wrong"]:badge;self.changedKeys=[NSSet setWithArray:extraBadgeEffect?@[@"badgeEmblem",@"name"]:@[@"badgeEmblem"]];}
@end
'''
MAIN=r'''
int main(){@autoreleasepool{
 NSString *source=@"11111111-1111-4111-8111-111111111111",*list=@"22222222-2222-4222-8222-222222222222";
 fixture=[FixtureReminder new];fixture.account=[FixtureAccount new];fixture.account.remObjectID=Identity(@"REMCDAccount",source);fixture.list=[FixtureList new];fixture.list.remObjectID=Identity(@"REMCDList",list);fixture.list.account=fixture.account;fixture.list.storage=[FixtureListStorage new];fixture.list.storage.accountID=fixture.account.remObjectID;moveLists=@[fixture.list];moveReminders=@[];
 NSDictionary *identity=@{@"source_id":source,@"list_id":list,@"include_badge":@YES};NSData *identityData=[NSJSONSerialization dataWithJSONObject:identity options:0 error:NULL];
 NSMutableDictionary *request=[@{@"source_id":source,@"list_id":list,@"badge":@{@"kind":@"emoji",@"value":@"🧪"},@"expected_features":[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(identityData) options:0 error:NULL]} mutableCopy];__block int gates=0;
 NSDictionary *(^call)(BOOL)=^NSDictionary *(BOOL allow){return [NSJSONSerialization JSONObjectWithData:ICloudRemindersSetListBadge([NSJSONSerialization dataWithJSONObject:request options:0 error:NULL],^{gates++;return allow;}) options:0 error:NULL];};
 NSDictionary *closed=call(NO);NSCAssert([closed[@"category"] isEqual:@"native_dispatch_cancelled"] && gates==1 && flagSaves==0,@"closed badge gate never saves: %@",closed);
 NSDictionary *good=call(YES);NSCAssert([good[@"result"][@"badge"] isEqual:request[@"badge"]] && flagSaves==1 && gates==2,@"exact emoji saved once: %@",good);int before=gates;
 extraBadgeEffect=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"unexpected staged field refused");extraBadgeEffect=NO;
 wrongBadge=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"setter mismatch refused");wrongBadge=NO;
 extraTracked=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"extra reminder refused");extraTracked=NO;
 wrongTracked=YES;NSCAssert(call(YES)[@"error"] && gates==before,@"tracked wrapper mismatch refused");wrongTracked=NO;
 fixture.list.isShared=YES;request[@"expected_features"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(identityData) options:0 error:NULL];NSCAssert(call(YES)[@"error"] && gates==before,@"shared list refused even with matching before");fixture.list.isShared=NO;request[@"expected_features"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(identityData) options:0 error:NULL];
 for(NSDictionary *invalid in @[@{@"kind":@"emoji",@"value":@"text"},@{@"kind":@"symbol",@"value":@"missing.symbol"},@{@"kind":@"default",@"value":@"extra"}]){request[@"badge"]=invalid;NSCAssert(call(YES)[@"error"] && gates==before,@"invalid badge refuses before gate");}
 request[@"badge"]=@{@"kind":@"symbol",@"value":@"star.fill"};NSCAssert([call(YES)[@"result"][@"badge"] isEqual:request[@"badge"]] && flagSaves==2,@"system symbol saves once");
 fixture.list.appearanceContext.badge=[[FixtureBadge alloc]initWithEmoji:@"🧪"];request[@"expected_features"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(identityData) options:0 error:NULL];request[@"badge"]=@{@"kind":@"default"};NSCAssert([call(YES)[@"result"][@"badge"] isEqual:request[@"badge"]] && flagSaves==3,@"default reset saves once");
 request[@"badge"]=@{@"kind":@"emoji",@"value":@"🧪"};request[@"expected_features"]=[NSJSONSerialization JSONObjectWithData:ICloudRemindersReadListFeatures(identityData) options:0 error:NULL];NSCAssert(call(YES)[@"error"],@"no-op refuses");
 request[@"badge"]=@{@"kind":@"symbol",@"value":@"star.fill"};saveFails=YES;NSCAssert([call(YES)[@"mutation_dispatched"] isEqual:@YES] && flagSaves==4,@"failed save retains uncertainty");
}}
'''
def run():
    fixture=fixture_source().replace('@interface FixtureListChange:NSObject',EXTRA+'\n@interface FixtureListChange:NSObject\n@property FixtureAppearance *appearanceContext;\n-(id)changedKeys;')
    fixture=fixture.replace('@interface FixtureList:NSObject','@interface FixtureAppearance;\n@interface FixtureList:NSObject\n@property FixtureAppearance *appearanceContext;')
    # Forward declaration, not an Objective-C interface body.
    fixture=fixture.replace('@interface FixtureAppearance;','@class FixtureAppearance;')
    fixture=fixture.replace('@implementation FixtureList\n','@implementation FixtureList\n-(id)appearanceContext{if(!_appearanceContext)_appearanceContext=[NSClassFromString(@"FixtureAppearance") new];return _appearanceContext;}\n')
    fixture=fixture.replace('@implementation FixtureListChange','@implementation FixtureListChange\n-(id)changedKeys{return self.appearanceContext.changedKeys;}\n-(id)appearanceContext{if(!_appearanceContext)_appearanceContext=[FixtureAppearance new];return _appearanceContext;}')
    fixture=fixture.replace('@interface FixtureSave:NSObject','@interface FixtureSave:NSObject\n@property FixtureListChange *badgeChange;\n-(id)trackedListChangeItems;')
    fixture=fixture.replace('v.destination=list;return v;','v.destination=list;self.badgeChange=v;return v;')
    fixture=fixture.replace('@implementation FixtureSave','@implementation FixtureSave\n-(id)trackedListChangeItems{FixtureTracked *v=[FixtureTracked new];v.valueWithoutPerformingCopy=wrongTracked?[NSObject new]:self.badgeChange;return @{fixture.list.remObjectID:v};}')
    source=shipped_source().replace('NSClassFromString(@"REMListBadge")','NSClassFromString(@"FixtureBadge")').replace('NSClassFromString(@"NSImage")','NSClassFromString(@"FixtureImage")')
    with tempfile.TemporaryDirectory(prefix='native-list-badge-') as directory:
        p=Path(directory);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(source+'\n'+fixture+'\n'+MAIN)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
    return {'status':'passed','apple_store_opened':False,'emoji_symbol_default_single_save':True,'closed_gate_stale_state_and_unreviewed_changes_refused':True,'failed_save_remains_uncertain':True}
if __name__=='__main__':print(json.dumps(run()))
