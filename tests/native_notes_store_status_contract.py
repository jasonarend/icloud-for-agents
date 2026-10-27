"""Fixed SQLite schema-status probe on synthetic files; no Notes model or real home."""
import json,sqlite3,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run():
    with tempfile.TemporaryDirectory(prefix='notes-store-status-') as directory:
        p=Path(directory);database=p/'store.sqlite'
        with sqlite3.connect(database) as db:db.execute('CREATE TABLE synthetic(value TEXT)')
        database.chmod(0o600);before=database.read_bytes()
        source=(ROOT/'src/icloud_for_agents/native/notes-model.m').read_text()
        start=source.index('static NSString *NotesStorePath(void) {');end=source.index('NSData *ICloudNotesStoreStatus',start)
        source=source[:start]+'static NSString *NotesStorePath(void){return [NSString stringWithUTF8String:getenv("SYNTHETIC_NOTES_STORE")];}\n'+source[end:]
        source=source.replace('NSProcessInfo.processInfo.operatingSystemVersion.majorVersion','27')
        (p/'notes-model.h').write_text((ROOT/'src/icloud_for_agents/native/notes-model.h').read_text())
        (p/'test.m').write_text(source+'\nint main(){@autoreleasepool{NSData *data=ICloudNotesStoreStatus();fwrite(data.bytes,1,data.length,stdout);return 0;}}\n')
        result=subprocess.run(['/usr/bin/clang','-fobjc-arc','-framework','Foundation','-framework','CoreData','-lsqlite3',str(p/'test.m'),'-o',str(p/'probe')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        def call(path):
            result=subprocess.run([str(p/'probe')],env={'SYNTHETIC_NOTES_STORE':str(path)},capture_output=True,text=True,check=True,timeout=5);return json.loads(result.stdout)
        report=call(database);assert report['store_read_access']=='available',report
        assert report['model_context_opened'] is False and report['application_writes_enabled'] is False and report['model_write_access']=='unverified'
        assert report['automatic_prompt_expected'] is False and str(p) not in json.dumps(report)
        assert database.read_bytes()==before
        missing=p/'missing.sqlite';assert call(missing)['store_read_access']=='denied_or_unavailable';assert not missing.exists()
        link=p/'link.sqlite';link.symlink_to(database);assert call(link)['store_read_access']=='unsafe_store_file'
        database.chmod(0o666);assert call(database)['store_read_access']=='unsafe_store_file';database.chmod(0o600)
        invalid=p/'invalid.sqlite';invalid.write_text('synthetic invalid database');invalid.chmod(0o600);assert call(invalid)['store_read_access']=='denied_or_unavailable'
    value={'status':'passed','real_notes_data_access':False,'checks':['fixed_schema_metadata_only','sqlite_readonly_mode','no_model_context','missing_store_not_created','unsafe_symlink_and_permissions_refused','invalid_file_not_empty_success','no_file_paths_in_response','macos27_settings_not_automatic_prompt']};print(json.dumps(value));return value
if __name__=='__main__':run()
