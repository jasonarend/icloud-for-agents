"""Scoped native template listing against in-memory provider objects only."""
import json, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
@interface FakeID:NSObject
@property NSUUID *uuid; @property NSString *entityName;
@end
@implementation FakeID @end
@interface FakeAccount:NSObject
@property FakeID *remObjectID;
@end
@implementation FakeAccount @end
@interface FakeStorage:NSObject
@property FakeID *accountID; @property NSString *name;
@end
@implementation FakeStorage @end
@interface FakeTemplate:NSObject
@property FakeID *remObjectID; @property FakeStorage *storage;
@end
@implementation FakeTemplate @end
static NSArray *accounts;static NSArray *templates;
@interface FakeStore:NSObject
-(id)fetchAccountsWithError:(NSError **)error;
@end
@implementation FakeStore
-(id)fetchAccountsWithError:(NSError **)error{return accounts;}
@end
@interface FakeView:NSObject
-(id)initWithStore:(id)store;
-(id)fetchTemplatesInAccount:(id)account error:(NSError **)error;
@end
@implementation FakeView
-(id)initWithStore:(id)store{return [super init];}
-(id)fetchTemplatesInAccount:(id)account error:(NSError **)error{return templates;}
@end
static FakeID *ident(NSString *entity,NSString *uuid){FakeID *i=[FakeID new];i.entityName=entity;i.uuid=[[NSUUID alloc]initWithUUIDString:uuid];return i;}
int main(int argc,const char **argv){@autoreleasepool{
 NSString *mode=[NSString stringWithUTF8String:argv[1]],*source=@"11111111-1111-4111-8111-111111111111",*other=@"22222222-2222-4222-8222-222222222222";
 FakeAccount *account=[FakeAccount new];account.remObjectID=ident(@"REMCDAccount",source);accounts=@[account];
 FakeTemplate *a=[FakeTemplate new];a.remObjectID=ident(@"REMCDTemplate",@"33333333-3333-4333-8333-333333333333");a.storage=[FakeStorage new];a.storage.accountID=account.remObjectID;a.storage.name=@"Synthetic";
 FakeTemplate *b=[FakeTemplate new];b.remObjectID=ident(@"REMCDTemplate",@"44444444-4444-4444-8444-444444444444");b.storage=[FakeStorage new];b.storage.accountID=account.remObjectID;b.storage.name=@"Second";
 templates=@[b,a];if([mode isEqual:@"foreign"])a.storage.accountID=ident(@"REMCDAccount",other);if([mode isEqual:@"duplicate"])templates=@[a,a];if([mode isEqual:@"missing"])accounts=@[];
 NSDictionary *req=@{@"source_id":source,@"limit":@1,@"offset":@([mode isEqual:@"beyond"]?9:0)};
 NSData *data=[NSJSONSerialization dataWithJSONObject:req options:0 error:NULL];NSData *result=ICloudRemindersListTemplates(data);fwrite(result.bytes,1,result.length,stdout);return 0;
}}
'''
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text()
    a=source.index('static BOOL FlagReadInterface(void)');b=source.index('static id ObjectValue',a)
    source=source[:a]+'static BOOL FlagReadInterface(void){return YES;}\n'+source[b:]
    for real,fake in [('REMStore','FakeStore'),('REMTemplatesDataView','FakeView')]:source=source.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
    with tempfile.TemporaryDirectory(prefix='reminder-templates-contract-') as d:
        p=Path(d);(p/'reminders-model.h').write_text((ROOT/'src/icloud_for_agents/native/reminders-model.h').read_text());(p/'test.m').write_text(source+'\n'+FIXTURE)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation',str(p/'test.m'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        def call(mode):return json.loads(subprocess.run([str(p/'contract'),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
        page=call('valid');assert len(page['templates'])==1 and page['templates'][0]['title']=='Synthetic' and page['done'] is False and page['next_offset']==1 and page['data_mutated'] is False
        beyond=call('beyond');assert beyond['templates']==[] and beyond['done'] is True and beyond['next_offset']==9
        for mode in ('foreign','duplicate','missing'):assert call(mode).get('error'),mode
    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['scoped_identity','bounded_stable_page','foreign_duplicate_missing_refused','no_mutation']}))
if __name__=='__main__':run()
