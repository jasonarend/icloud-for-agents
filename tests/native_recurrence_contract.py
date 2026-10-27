"""Execute shipped Gregorian recurrence constructors and serializers without a store."""
import json,subprocess,tempfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from icloud_for_agents.native_recurrence import validate_recurrence,observed_rule

def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();start=source.index('func recurrenceInteger(')
    constructor=source[start:source.index('if action == "apply" {',start)];start=source.index('func recurrenceObject(');serializer=source[start:source.index('func alarmObject(',start)]
    fixture='''
let request=try JSONSerialization.jsonObject(with:FileHandle.standardInput.readDataToEndOfFile()) as! [String:Any]
var due=DateComponents();due.calendar=Calendar(identifier:.gregorian);due.year=2027;due.month=1;due.day=4;due.hour=12;due.minute=0;due.timeZone=TimeZone(identifier:"UTC")
let rule=reminderRecurrence(request["rule"]!,due:due)
let value:Any=rule.map {recurrenceObject($0)} ?? NSNull() as Any
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:value,options:[.fragmentsAllowed,.sortedKeys]))
'''
    with tempfile.TemporaryDirectory(prefix='native-rule-contract-') as directory:
        path=Path(directory);script=path/'rule.swift';binary=path/'rule'
        script.write_text('import Foundation\nimport EventKit\nfunc fail(_ reason:String)->Never {exit(3)}\n'+constructor+serializer+fixture)
        subprocess.run(['/usr/bin/swiftc',str(script),'-o',str(binary)],capture_output=True,check=True,timeout=60)
        def call(rule):return subprocess.run([str(binary)],input=json.dumps({'rule':rule}),capture_output=True,text=True,timeout=5)
        vectors=[{'frequency':f,'interval':1,'end':e} for f in ('daily','weekly','monthly','yearly') for e in (None,{'count':3},{'date':1830000000})]
        vectors += [{'frequency':'weekly','interval':2,'end':{'count':4},'days_of_week':[{'weekday':2,'week_number':0},{'weekday':6,'week_number':0}]},
                    {'frequency':'monthly','interval':1,'end':None,'days_of_month':[1,15,-1]},
                    {'frequency':'monthly','interval':2,'end':None,'days_of_week':[{'weekday':6,'week_number':-1}]},
                    {'frequency':'yearly','interval':1,'end':{'count':3},'months_of_year':[1,7]}]
        for vector in vectors:
            rule=validate_recurrence(vector);result=call(rule);assert result.returncode==0,(vector,result.stderr,result.returncode)
            assert observed_rule(json.loads(result.stdout))==rule,vector
        assert json.loads(call(None).stdout) is None
        base=validate_recurrence({'frequency':'daily','interval':1,'end':None})
        for change in ({'interval':True},{'interval':0},{'interval':-1},{'months_of_year':[1]},
                       {'end':{'count':True}},{'end':{'count':2,'date':1830000000}},{'end':{'date':1000000000}},
                       {'set_positions':[1]},{'unexpected':'value'}):
            assert call(dict(base,**change)).returncode==3,change
    return {'status':'passed','live_store_or_permission_used':False,'rule_roundtrips':len(vectors),'checks':['all_frequencies','count_date_and_indefinite_ends','weekly_weekdays_and_interval','monthly_days_and_ordinal_weekday','yearly_months','invalid_ignored_and_ambiguous_fields_refused']}
if __name__=='__main__':
    result=run();(ROOT/'build/reports/NATIVE-RECURRENCE-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
