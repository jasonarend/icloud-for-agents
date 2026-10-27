"""Shipped time-alarm construction on in-memory EventKit objects, with no store."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text()
    start=source.index('func ordinaryTimeAlarm(');functions=source[start:source.index('// Gregorian recurrence configuration',start)]
    start=source.index('func alarmObject(');serializer=source[start:source.index('func object(_ reminder:',start)]
    fixture='''
let request=try JSONSerialization.jsonObject(with:FileHandle.standardInput.readDataToEndOfFile()) as! [String:Any]
var due:DateComponents?=nil
if request["timed"] as? Bool == true {var d=DateComponents();d.year=2027;d.month=1;d.day=4;d.hour=12;d.minute=0;d.timeZone=TimeZone(identifier:"UTC");due=d}
let alarms=reminderTimeAlarms(request["alarms"]!,due:due)
precondition(alarms.allSatisfy(ordinaryTimeAlarm))
let email=EKAlarm(relativeOffset:0);email.emailAddress="synthetic@example.com";precondition(!ordinaryTimeAlarm(email))
let sound=EKAlarm(relativeOffset:0);sound.soundName="Synthetic";precondition(!ordinaryTimeAlarm(sound))
let location=EKAlarm(relativeOffset:0);location.structuredLocation=EKStructuredLocation(title:"Synthetic place");precondition(!ordinaryTimeAlarm(location))
let place=EKStructuredLocation(title:"Synthetic location");place.geoLocation=CLLocation(latitude:12.25,longitude:-34.5);place.radius=250
let arriving=EKAlarm(relativeOffset:0);arriving.structuredLocation=place;arriving.proximity = .enter
let leaving=EKAlarm(relativeOffset:0);leaving.structuredLocation=place;leaving.proximity = .leave
let a=alarmObject(arriving),b=alarmObject(leaving),p=a["structured_location"] as! [String:Any]
precondition((p["title"] as? String)=="Synthetic location" && (p["latitude"] as? Double)==12.25 && (p["longitude"] as? Double)==(-34.5) && (p["radius_meters"] as? Double)==250)
precondition((a["proximity"] as? Int)==EKAlarmProximity.enter.rawValue && (b["proximity"] as? Int)==EKAlarmProximity.leave.rawValue)
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:alarms.map(alarmObject)))
'''
    with tempfile.TemporaryDirectory(prefix='native-time-alarms-') as directory:
        path=Path(directory);script=path/'alarms.swift';binary=path/'alarms'
        script.write_text('import Foundation\nimport EventKit\nimport CoreLocation\nfunc fail(_ reason:String)->Never {exit(3)}\n'+functions+serializer+fixture)
        subprocess.run(['/usr/bin/swiftc',str(script),'-o',str(binary)],capture_output=True,check=True,timeout=60)
        def call(alarms,timed=True):return subprocess.run([str(binary)],input=json.dumps({'alarms':alarms,'timed':timed}),capture_output=True,text=True,timeout=5)
        result=call([{'absolute_date':1800000000},{'relative_offset_seconds':-900}]);assert result.returncode==0,result.stderr
        values=json.loads(result.stdout);assert values[0]['absolute_date']==1800000000 and values[1]['relative_offset_seconds']==-900
        assert json.loads(call([]).stdout)==[]
        for alarms in (None,[{'absolute_date':True}],[{'absolute_date':0}],[{'absolute_date':1800000000.5}],
                       [{'relative_offset_seconds':31536001}],[{'absolute_date':1800000000}]*2,
                       [{'absolute_date':1800000000+i} for i in range(11)],[{'proximity':'entry'}]):assert call(alarms).returncode==3
        assert call([{'relative_offset_seconds':-900}],timed=False).returncode==3
    return {'status':'passed','live_store_or_permission_used':False,'checks':['absolute_and_relative_time_alarms','empty_set','precise_finite_bounded_integer_seconds','relative_timezone_requirement','duplicates_and_unknown_triggers_refused','advanced_alarms_distinguished','synthetic_geofence_read_projection']}

if __name__=='__main__':
    result=run();(ROOT/'build/reports/NATIVE-REMINDER-ALARMS-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
