"""Native source-filter staging with fake provider objects and the real scope guard."""
import json
from pathlib import Path
import subprocess
import tempfile
from native_smart_rename_contract import ROOT, FIXTURE

EXTRA = r'''
@interface FakeAccount:NSObject
@property FakeID *remObjectID;
@end
@implementation FakeAccount @end
static NSDictionary *physical;
@interface FakeList(Scope)
-(id)account;-(BOOL)isShared;-(BOOL)isSharedToMe;
@end
@implementation FakeList(Scope)
-(id)account{FakeAccount *a=[FakeAccount new];a.remObjectID=self.storage.accountID;return a;}
-(BOOL)isShared{return [mode isEqual:@"shared"];}-(BOOL)isSharedToMe{return NO;}
@end
@interface FakeStorage(Scope)
-(BOOL)shouldCategorizeGroceryItems;-(BOOL)shouldSupportGroceryShoppingSession;-(BOOL)isGroup;
@end
@implementation FakeStorage(Scope)
-(BOOL)shouldCategorizeGroceryItems{return [mode isEqual:@"grocery"];}
-(BOOL)shouldSupportGroceryShoppingSession{return NO;}-(BOOL)isGroup{return NO;}
@end
@interface FakeStore(Scope)
-(id)fetchListWithObjectID:(id)identity error:(NSError**)error;
@end
@implementation FakeStore(Scope)
-(id)fetchListWithObjectID:(FakeID*)identity error:(NSError**)error{return physical[identity.uuid.UUIDString];}
@end
@interface FakeChange(Filter)
-(void)setFilterData:(id)value;
@end
@implementation FakeChange(Filter)
-(void)setFilterData:(id)value{self.storage.filterData=value;[self.changedKeys addObject:@"filterData"];
 if([mode isEqual:@"extra_key"])[self.changedKeys addObject:@"name"];
 if([mode isEqual:@"filter_drift"])self.storage.filterData=[@"{}"dataUsingEncoding:NSUTF8StringEncoding];
 if([mode isEqual:@"title_drift"])self.storage.name=@"Unreviewed title";
}
@end
static NSDictionary *scope(NSString *source,NSString *identity){return @{@"list":@{@"id":identity,@"source_id":source},@"role":@{@"source_id":source,@"list_id":identity,@"grocery_categorization":@NO,@"grocery_shopping_session":@NO,@"is_group":@NO,@"shared":@NO,@"shared_to_me":@NO}};}
'''
CASES = r'''
int main(int argc,const char **argv){@autoreleasepool{
 mode=[NSString stringWithUTF8String:argv[1]];NSString *source=@"11111111-1111-4111-8111-111111111111",*identifier=@"22222222-2222-4222-8222-222222222222",*old=@"33333333-3333-4333-8333-333333333333",*next=@"44444444-4444-4444-8444-444444444444";
 original=[FakeList new];original.remObjectID=ident(@"REMCDSmartList",identifier);original.storage=[FakeStorage new];original.storage.remObjectID=original.remObjectID;original.storage.accountID=ident(@"REMCDAccount",source);original.storage.name=@"Synthetic source view";
 NSMutableDictionary *criterion=[@{@"operation":@"and",@"lists":@{@"include":@[old],@"exclude":@[]}}mutableCopy];if([mode isEqual:@"unknown_criteria"])criterion[@"unknown"]=@YES;if([mode isEqual:@"or"])criterion[@"operation"]=@"or";
 original.storage.filterData=[NSJSONSerialization dataWithJSONObject:criterion options:0 error:NULL];
 NSMutableDictionary *objects=[NSMutableDictionary dictionary];for(NSString *identity in @[old,next]){FakeList *list=[FakeList new];list.remObjectID=ident(@"REMCDList",identity);list.storage=[FakeStorage new];list.storage.remObjectID=list.remObjectID;list.storage.accountID=ident(@"REMCDAccount",source);objects[identity]=list;}physical=objects;
 if([mode isEqual:@"foreign_reference"])((FakeList*)objects[next]).storage.accountID=ident(@"REMCDAccount",identifier);
 id before=[NSJSONSerialization JSONObjectWithData:ICloudRemindersSmartListDefinition([NSData data])options:0 error:NULL];
 NSMutableDictionary *request=[@{@"source_id":source,@"id":identifier,@"list_ids":@[next],@"before":before,@"scopes":@[scope(source,old),scope(source,next)]}mutableCopy];
 if([mode isEqual:@"missing_old"])request[@"scopes"]=@[scope(source,next)];
 if([mode isEqual:@"duplicate"])request[@"list_ids"]=@[next,next];
 if([mode isEqual:@"empty"])request[@"list_ids"]=@[];
 if([mode isEqual:@"unreviewed"])request[@"list_ids"]=@[@"55555555-5555-4555-8555-555555555555"];
 if([mode isEqual:@"same_sources"])request[@"list_ids"]=@[old];
 if([mode isEqual:@"multiple"])request[@"list_ids"]=@[old,next];
 if([mode isEqual:@"extra_field"])request[@"extra"]=@YES;
 NSData *reply=ICloudRemindersSetSmartListSources(EncodeFlag(request),^BOOL{gates++;return ![mode isEqual:@"closed"];});
 id response=[NSJSONSerialization JSONObjectWithData:reply options:0 error:NULL];NSData *out=EncodeFlag(@{@"response":response,@"gate_calls":@(gates),@"save_calls":@(saves)});fwrite(out.bytes,1,out.length,stdout);return 0;
}}
'''


def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text()
    prefix=source[:source.index('static BOOL FlagReadInterface(void)')].replace('#import "reminders-model.h"','#import <Foundation/Foundation.h>')
    helpers=source[source.index('static id ObjectValue'):source.index('static id SelectedFailure')]
    scope=source[source.index('static BOOL LifecycleListPrivate'):source.index('NSData *ICloudRemindersRemoveRootLeaf')]
    writer=source[source.index('NSData *ICloudRemindersSetSmartListSources'):]
    for real,fake in [('REMStore','FakeStore'),('REMObjectID','FakeID'),('REMSmartListsDataView','FakeView'),('REMSaveRequest','FakeSave')]:
        writer=writer.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
    with tempfile.TemporaryDirectory(prefix='smart-sources-contract-') as directory:
        path=Path(directory);script=path/'main.m';binary=path/'contract';script.write_text(prefix+helpers+FIXTURE+EXTRA+scope+writer+CASES)
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(script),'-o',str(binary)],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        def call(mode):return json.loads(subprocess.run([str(binary),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
        closed=call('closed');assert closed['gate_calls']==1 and closed['save_calls']==0 and closed['response']['error']
        opened=call('valid');assert opened['gate_calls']==1 and opened['save_calls']==1 and opened['response']['result']['saved_locally'] is True
        multiple=call('multiple');assert multiple['gate_calls']==1 and multiple['save_calls']==1 and multiple['response']['result']['saved_locally'] is True
        error=call('save_error');assert error['save_calls']==1 and error['response']['mutation_dispatched'] is True
        for mode in ('shared','grocery','foreign_reference','unknown_criteria','or','missing_old','duplicate','empty','unreviewed','same_sources','extra_field','extra_key','filter_drift','title_drift','unrelated_reminder'):
            value=call(mode);assert value['gate_calls']==0 and value['save_calls']==0 and value['response']['error'],mode
    return {'status':'passed','apple_store_opened':False,'exact_source_and_private_references_verified':True,
            'known_schema_only':True,'closed_gate_and_single_save_verified':True,'unrelated_changes_refused':True}


if __name__=='__main__':print(json.dumps(run()))
