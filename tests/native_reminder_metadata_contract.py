"""Run shipped EventKit metadata serializers on in-memory objects, without a store."""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text()
    functions=source[source.index('func recurrenceObject('):source.index('func object(_ reminder:')]
    fixture='''
let day=EKRecurrenceDayOfWeek(.monday)
let end=EKRecurrenceEnd(occurrenceCount:5)
let rule=EKRecurrenceRule(recurrenceWith:.weekly,interval:2,daysOfTheWeek:[day],daysOfTheMonth:nil,
    monthsOfTheYear:nil,weeksOfTheYear:nil,daysOfTheYear:nil,setPositions:nil,end:end)
let relative=EKAlarm(relativeOffset:-900)
let absolute=EKAlarm(absoluteDate:Date(timeIntervalSince1970:1234567))
let result:[String:Any]=["rule":recurrenceObject(rule),"relative":alarmObject(relative),"absolute":alarmObject(absolute)]
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:result,options:[.sortedKeys]))
'''
    with tempfile.TemporaryDirectory(prefix='icloud-native-metadata-') as directory:
        script=Path(directory)/'metadata.swift';binary=Path(directory)/'metadata'
        script.write_text('import Foundation\nimport EventKit\n'+functions+fixture)
        subprocess.run(['/usr/bin/swiftc',str(script),'-o',str(binary)],capture_output=True,check=True,timeout=60)
        process=subprocess.run([str(binary)],capture_output=True,check=True,timeout=10)
        value=json.loads(process.stdout)
        assert value['rule']['frequency']=='weekly' and value['rule']['interval']==2
        assert value['rule']['days_of_week']==[{'weekday':2,'week_number':0}]
        assert value['rule']['end']=={'count':5,'date':None}
        assert value['relative']['relative_offset_seconds']==-900
        assert value['relative']['absolute_date'] is None
        assert value['absolute']['absolute_date']==1234567
    return {'status':'passed','in_memory_framework_objects_only':True,
            'live_store_or_permission_used':False,'checks':['weekly_interval_and_weekday','count_end_condition','relative_alarm','absolute_alarm']}


if __name__=='__main__':
    result=run();(ROOT/'build/reports').mkdir(parents=True,exist_ok=True)
    (ROOT/'build/reports/NATIVE-REMINDER-METADATA-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
