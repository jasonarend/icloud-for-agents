"""Compile the shipped template name-only save against synthetic native objects."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
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
@interface FakeStorage:NSObject<NSCopying>
@property id publicLink;@property long long minimumSupportedVersion;@property FakeID *remObjectID;@property FakeID *accountID;@property NSString *name;@property NSData *filterData;
@end
@implementation FakeStorage
-(id)copyWithZone:(NSZone*)zone{if([mode isEqual:@"bad_copy"])return self;FakeStorage *s=[FakeStorage new];s.publicLink=self.publicLink;s.minimumSupportedVersion=self.minimumSupportedVersion;s.remObjectID=self.remObjectID;s.accountID=self.accountID;s.name=self.name;s.filterData=self.filterData;return s;}
-(BOOL)isEqual:(id)value{if(![value isKindOfClass:FakeStorage.class])return NO;FakeStorage *v=value;return self.publicLink==v.publicLink && self.minimumSupportedVersion==v.minimumSupportedVersion && self.remObjectID==v.remObjectID && self.accountID==v.accountID && [self.name isEqual:v.name] && [self.filterData isEqual:v.filterData];}
@end
@interface FakeList:NSObject
@property FakeID *remObjectID;@property FakeStorage *storage;
@end
@implementation FakeList @end
static FakeList *original;
@interface FakeView:NSObject
-(id)initWithStore:(id)store;
-(id)fetchTemplateWithObjectID:(id)identity error:(NSError**)error;
@end
@implementation FakeView
-(id)initWithStore:(id)store{return [super init];}
-(id)fetchTemplateWithObjectID:(id)identity error:(NSError**)error{return original;}
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
 if([mode isEqual:@"version_drift"])self.storage.minimumSupportedVersion=0;
 if([mode isEqual:@"extra_key"])[self.changedKeys addObject:@"filterData"];
}
@end
@interface FakeWrapper:NSObject
@property FakeChange *valueWithoutPerformingCopy;
@end
@implementation FakeWrapper @end
@interface FakeSave:NSObject
@property NSDictionary *trackedTemplateChangeItems;
-(id)initWithStore:(id)store;
-(id)updateTemplate:(id)list;
-(BOOL)saveSynchronouslyWithError:(NSError**)error;
-(id)trackedAccountChangeItems;-(id)trackedListChangeItems;-(id)trackedListSectionChangeItems;
-(id)trackedReminderChangeItems;-(id)trackedSmartListSectionChangeItems;
-(id)trackedSmartListChangeItems;-(id)trackedTemplateSectionChangeItems;
@end
@implementation FakeSave
-(id)initWithStore:(id)store{return [super init];}
-(id)updateTemplate:(FakeList*)list{FakeStorage *s=[FakeStorage new];s.remObjectID=list.storage.remObjectID;s.accountID=list.storage.accountID;s.name=list.storage.name;s.filterData=list.storage.filterData;s.minimumSupportedVersion=list.storage.minimumSupportedVersion;FakeChange *c=[FakeChange new];c.storage=s;c.changedKeys=[NSMutableSet set];FakeWrapper *w=[FakeWrapper new];w.valueWithoutPerformingCopy=c;self.trackedTemplateChangeItems=@{list.remObjectID:w};return c;}
-(BOOL)saveSynchronouslyWithError:(NSError**)error{saves++;if([mode isEqual:@"save_error"]){*error=[NSError errorWithDomain:@"synthetic"code:1 userInfo:nil];return NO;}return YES;}
-(id)trackedAccountChangeItems{return @{};}-(id)trackedListChangeItems{return @{};}
-(id)trackedListSectionChangeItems{return @{};}-(id)trackedReminderChangeItems{return [mode isEqual:@"unrelated_reminder"]?@{@"unreviewed":@1}:@{};}
-(id)trackedSmartListSectionChangeItems{return @{};}-(id)trackedSmartListChangeItems{return @{};}-(id)trackedTemplateSectionChangeItems{return @{};}
@end
static FakeID *ident(NSString *entity,NSString *uuid){FakeID *i=[FakeID new];i.entityName=entity;i.uuid=[[NSUUID alloc]initWithUUIDString:uuid];return i;}
static NSData *ICloudRemindersTemplateMetadata(NSData *data){
 return EncodeFlag(@{@"protocol_version":@1,@"source_id":[original.storage.accountID.uuid UUIDString],@"id":[original.remObjectID.uuid UUIDString],@"title":original.storage.name,@"minimum_supported_version":@(original.storage.minimumSupportedVersion),@"configuration":NSNull.null,@"public_link_present":@((BOOL)(original.storage.publicLink!=nil)),@"public_link_included":@NO,@"public_link":NSNull.null,@"data_mutated":@NO,@"backend":@"reminderkit-template-metadata-v1"});
}
static NSData *ICloudRemindersTemplateSections(NSData *data){return EncodeFlag(@{@"protocol_version":@1,@"sections":@[]});}
'''

CASES = r'''
int main(int argc,const char **argv){@autoreleasepool{
mode=[NSString stringWithUTF8String:argv[1]];NSString *source=@"11111111-1111-4111-8111-111111111111",*identifier=@"22222222-2222-4222-8222-222222222222";
original=[FakeList new];original.remObjectID=ident(@"REMCDTemplate",identifier);original.storage=[FakeStorage new];original.storage.remObjectID=original.remObjectID;original.storage.accountID=ident(@"REMCDAccount",source);original.storage.name=@"Synthetic original";original.storage.minimumSupportedVersion=20220430;original.storage.filterData=[@"{\"unknown_filter\":true}"dataUsingEncoding:NSUTF8StringEncoding];
id before=[NSJSONSerialization JSONObjectWithData:ICloudRemindersTemplateMetadata([NSData data])options:0 error:NULL];
NSMutableDictionary *request=[@{@"source_id":source,@"id":identifier,@"title":@"Synthetic renamed",@"before_metadata":before,@"before_sections":[NSJSONSerialization JSONObjectWithData:ICloudRemindersTemplateSections([NSData data])options:0 error:NULL]}mutableCopy];
if([mode isEqual:@"shared"])original.storage.publicLink=@"unfetched";
if([mode isEqual:@"stale_title"])original.storage.name=@"Changed independently";
if([mode isEqual:@"foreign_source"])request[@"source_id"]=@"33333333-3333-4333-8333-333333333333";
if([mode isEqual:@"physical_list"])original.remObjectID.entityName=@"REMCDList";
if([mode isEqual:@"wrong_storage"])original.storage.remObjectID=ident(@"REMCDTemplate",@"33333333-3333-4333-8333-333333333333");
if([mode isEqual:@"extra_field"])request[@"extra"]=@YES;
if([mode isEqual:@"same_title"])request[@"title"]=before[@"title"];
NSData *result=ICloudRemindersRenameTemplate(EncodeFlag(request),^BOOL{gates++;return ![mode isEqual:@"closed"];});
id response=[NSJSONSerialization JSONObjectWithData:result options:0 error:NULL];NSData *out=EncodeFlag(@{@"response":response,@"gate_calls":@(gates),@"save_calls":@(saves)});fwrite(out.bytes,1,out.length,stdout);return 0;
}}
'''

def run():
 source=(ROOT/'src/icloud_for_agents/native/reminders-model.m').read_text()
 prefix=source[:source.index('static BOOL FlagReadInterface(void)')].replace('#import "reminders-model.h"','#import <Foundation/Foundation.h>')
 support=source[source.index('static id ObjectValue'):source.index('static id SelectedFailure')]
 writer=source[source.index('NSData *ICloudRemindersRenameTemplate'):source.index('// Read ordinary native sections')]
 for real,fake in [('REMSaveRequest','FakeSave'),('REMTemplatesDataView','FakeView'),('REMStore','FakeStore'),('REMObjectID','FakeID')]:writer=writer.replace('NSClassFromString(@"'+real+'")','NSClassFromString(@"'+fake+'")')
 with tempfile.TemporaryDirectory(prefix='template-rename-contract-')as directory:
  p=Path(directory);src=p/'main.m';binary=p/'check';src.write_text(prefix+support+FIXTURE+writer+CASES)
  built=subprocess.run(['/usr/bin/clang','-fobjc-arc','-fblocks','-framework','Foundation',str(src),'-o',str(binary)],capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
  def call(mode):return json.loads(subprocess.run([str(binary),mode],capture_output=True,text=True,check=True,timeout=5).stdout)
  closed=call('closed');assert closed['gate_calls']==1 and closed['save_calls']==0 and closed['response']['error']
  opened=call('valid');assert opened['save_calls']==1 and opened['gate_calls']==1 and opened['response']['result']['saved_locally']is True
  failed=call('save_error');assert failed['save_calls']==1 and failed['response']['mutation_dispatched']is True
  for mode in('shared','bad_copy','version_drift','filter_drift','extra_key','unrelated_reminder','stale_title','foreign_source','physical_list','wrong_storage','extra_field','same_title'):
   value=call(mode);assert value['save_calls']==0 and value['gate_calls']==0 and value['response']['error'],mode
 return {'status':'passed','apple_store_opened':False,'native_template_only_name_save_and_closed_gate':True,'complete_storage_copy_and_other_trackers_preserved':True,'shared_scope_identity_drift_unrelated_changes_refused':True,'terminal_save_error_retained':True}
if __name__=='__main__':print(json.dumps(run()))
