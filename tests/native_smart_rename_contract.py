"""Compile the shipped Smart List rename over isolated native-object fixtures."""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
static NSString *mode;static NSUInteger saves=0,gates=0;
@interface FakeID:NSObject<NSCopying>
@property NSUUID *uuid;@property NSString *entityName;
+(id)objectIDWithURL:(NSURL *)url;
@end
@implementation FakeID
+(id)objectIDWithURL:(NSURL*)url{FakeID *i=[FakeID new];i.uuid=[[NSUUID alloc]initWithUUIDString:url.lastPathComponent];i.entityName=url.host;return i;}
-(id)copyWithZone:(NSZone*)zone{return self;}
@end
@interface FakeStorage:NSObject
@property FakeID *remObjectID;@property FakeID *accountID;@property NSString *name;@property NSData *filterData;
@end
@implementation FakeStorage @end
@interface FakeList:NSObject
@property FakeID *remObjectID;@property FakeStorage *storage;
@end
@implementation FakeList @end
static FakeList *original;
@interface FakeView:NSObject
-(id)initWithStore:(id)store;
-(id)fetchCustomSmartListWithObjectID:(id)identity error:(NSError**)error;
@end
@implementation FakeView
-(id)initWithStore:(id)store{return [super init];}
-(id)fetchCustomSmartListWithObjectID:(id)identity error:(NSError**)error{return original;}
@end
@interface FakeStore:NSObject @end
@implementation FakeStore @end
@interface FakeChange:NSObject
@property FakeStorage *storage;@property NSMutableSet *changedKeys;
-(void)setName:(id)value;
@end
@implementation FakeChange
-(void)setName:(id)value{self.storage.name=value;[self.changedKeys addObject:@"name"];
 if([mode isEqual:@"filter_drift"])self.storage.filterData=[@"{}"dataUsingEncoding:NSUTF8StringEncoding];
 if([mode isEqual:@"extra_key"])[self.changedKeys addObject:@"filterData"];
}
@end
@interface FakeWrapper:NSObject
@property FakeChange *valueWithoutPerformingCopy;
@end
@implementation FakeWrapper @end
@interface FakeSave:NSObject
@property NSDictionary *trackedSmartListChangeItems;
-(id)initWithStore:(id)store;
-(id)updateSmartList:(id)list;
-(BOOL)saveSynchronouslyWithError:(NSError**)error;
-(id)trackedAccountChangeItems;-(id)trackedListChangeItems;-(id)trackedListSectionChangeItems;
-(id)trackedReminderChangeItems;-(id)trackedSmartListSectionChangeItems;
-(id)trackedTemplateChangeItems;-(id)trackedTemplateSectionChangeItems;
@end
@implementation FakeSave
-(id)initWithStore:(id)store{return [super init];}
-(id)updateSmartList:(FakeList*)list{FakeStorage *s=[FakeStorage new];s.remObjectID=list.storage.remObjectID;s.accountID=list.storage.accountID;s.name=list.storage.name;s.filterData=list.storage.filterData;FakeChange *c=[FakeChange new];c.storage=s;c.changedKeys=[NSMutableSet set];FakeWrapper *w=[FakeWrapper new];w.valueWithoutPerformingCopy=c;self.trackedSmartListChangeItems=@{list.remObjectID:w};return c;}
-(BOOL)saveSynchronouslyWithError:(NSError**)error{saves++;if([mode isEqual:@"save_error"]){*error=[NSError errorWithDomain:@"synthetic"code:1 userInfo:nil];return NO;}return YES;}
-(id)trackedAccountChangeItems{return @{};}-(id)trackedListChangeItems{return @{};}
-(id)trackedListSectionChangeItems{return @{};}-(id)trackedReminderChangeItems{return [mode isEqual:@"unrelated_reminder"]?@{@"unreviewed":@1}:@{};}
-(id)trackedSmartListSectionChangeItems{return @{};}-(id)trackedTemplateChangeItems{return @{};}-(id)trackedTemplateSectionChangeItems{return @{};}
@end
static FakeID *ident(NSString *entity,NSString *uuid){FakeID *i=[FakeID new];i.entityName=entity;i.uuid=[[NSUUID alloc]initWithUUIDString:uuid];return i;}
static NSData *ICloudRemindersSmartListDefinition(NSData *data){
 return EncodeFlag(@{@"protocol_version":@1,@"source_id":[original.storage.accountID.uuid UUIDString],@"id":[original.remObjectID.uuid UUIDString],@"title":original.storage.name,@"definition_json":[[NSString alloc]initWithData:original.storage.filterData encoding:NSUTF8StringEncoding],@"data_mutated":@NO,@"backend":@"reminderkit-smart-definition-v1"});
}
'''
CASES = r'''
int main(int argc,const char **argv){@autoreleasepool{
mode=[NSString stringWithUTF8String:argv[1]];NSString *source=@"11111111-1111-4111-8111-111111111111",*identifier=@"22222222-2222-4222-8222-222222222222";
original=[FakeList new];original.remObjectID=ident(@"REMCDSmartList",identifier);original.storage=[FakeStorage new];original.storage.remObjectID=original.remObjectID;original.storage.accountID=ident(@"REMCDAccount",source);original.storage.name=@"Synthetic original";original.storage.filterData=[@"{\"unknown_filter\":true}"dataUsingEncoding:NSUTF8StringEncoding];
id before=[NSJSONSerialization JSONObjectWithData:ICloudRemindersSmartListDefinition([NSData data])options:0 error:NULL];
NSMutableDictionary *request=[@{@"source_id":source,@"id":identifier,@"title":@"Synthetic renamed",@"before":before}mutableCopy];
if([mode isEqual:@"stale_title"])original.storage.name=@"Changed independently";
if([mode isEqual:@"foreign_source"])request[@"source_id"]=@"33333333-3333-4333-8333-333333333333";
if([mode isEqual:@"physical_list"])original.remObjectID.entityName=@"REMCDList";
if([mode isEqual:@"wrong_storage"])original.storage.remObjectID=ident(@"REMCDSmartList",@"33333333-3333-4333-8333-333333333333");
if([mode isEqual:@"extra_field"])request[@"extra"]=@YES;
if([mode isEqual:@"same_title"])request[@"title"]=before[@"title"];
NSData *result=ICloudRemindersRenameSmartList(EncodeFlag(request),^BOOL{gates++;return ![mode isEqual:@"closed"];});
id response=[NSJSONSerialization JSONObjectWithData:result options:0 error:NULL];NSData *out=EncodeFlag(@{@"response":response,@"gate_calls":@(gates),@"save_calls":@(saves)});fwrite(out.bytes,1,out.length,stdout);return 0;
}}
'''


def run():
    source = (ROOT / 'src/icloud_for_agents/native/reminders-model.m').read_text()
    prefix = source[:source.index('static BOOL FlagReadInterface(void)')].replace('#import "reminders-model.h"', '#import <Foundation/Foundation.h>')
    support = source[source.index('static id ObjectValue'):source.index('static id SelectedFailure')]
    writer = source[source.index('NSData *ICloudRemindersRenameSmartList'):source.index('// Observe sections of one exact')]
    for real, fake in [('REMSaveRequest', 'FakeSave'), ('REMSmartListsDataView', 'FakeView'), ('REMStore', 'FakeStore'), ('REMObjectID', 'FakeID')]:
        writer = writer.replace('NSClassFromString(@"' + real + '")', 'NSClassFromString(@"' + fake + '")')
    with tempfile.TemporaryDirectory(prefix='smart-rename-contract-') as directory:
        path = Path(directory);script = path / 'main.m';binary = path / 'contract'
        script.write_text(prefix + support + FIXTURE + writer + CASES)
        result = subprocess.run(['/usr/bin/clang', '-fobjc-arc', '-fblocks', '-framework', 'Foundation', str(script), '-o', str(binary)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        def call(mode):return json.loads(subprocess.run([str(binary), mode], capture_output=True, text=True, check=True, timeout=5).stdout)
        closed = call('closed');assert closed['gate_calls'] == 1 and closed['save_calls'] == 0 and closed['response']['error']
        opened = call('valid');assert opened['save_calls'] == 1 and opened['gate_calls'] == 1 and opened['response']['result']['saved_locally'] is True
        failed = call('save_error');assert failed['save_calls'] == 1 and failed['response']['mutation_dispatched'] is True
        for mode in ('filter_drift', 'extra_key', 'unrelated_reminder', 'stale_title', 'foreign_source', 'physical_list', 'wrong_storage', 'extra_field', 'same_title'):
            value = call(mode);assert value['save_calls'] == 0 and value['gate_calls'] == 0 and value['response']['error'], mode
    # Run the actual synchronous dispatch prefix with a clock that advances
    # while the native staging call would block. No native object is opened.
    swift = (ROOT / 'src/icloud_for_agents/native/reminders.swift').read_text()
    gate = swift[swift.index('@objc(ICloudAgentsMutationGate)'):swift.index('func respond(')]
    gate = gate.replace('ProcessInfo.processInfo.systemUptime', 'syntheticUptime')
    begin = swift.index('if action == "apply" {\n')
    end = swift.index('    if let item=req["change"] as? [String:Any],item["operation"] as? String=="rename_smart_list"', begin)
    dispatch = swift[begin:end] + '\n}\n'
    program = ('import Foundation\nvar syntheticUptime:TimeInterval=100\nvar privateReminderGateArmed=false\n'
               'let action="apply"\nlet req:[String:Any]=["change":["operation":"rename_smart_list"]]\n'
               + gate + dispatch + '\nprecondition(privateReminderGateArmed)\nsyntheticUptime=126\n'
               'precondition(!NativeMutationGate.beginMutation())\nprecondition(!NativeMutationGate.cancel())\n')
    with tempfile.TemporaryDirectory(prefix='smart-rename-deadline-') as directory:
        path = Path(directory);script = path / 'main.swift';binary = path / 'contract';script.write_text(program)
        result = subprocess.run(['/usr/bin/swiftc', str(script), '-o', str(binary)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=5)
    return {'status': 'passed', 'apple_store_opened': False, 'closed_gate_and_single_save_verified': True,
            'scope_filter_key_drift_and_unrelated_change_refused': True, 'save_error_retains_uncertainty': True,
            'synchronous_dispatch_arms_deadline_and_refuses_late_entry': True}


if __name__ == '__main__':print(json.dumps(run()))
