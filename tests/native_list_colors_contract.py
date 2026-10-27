"""Actual AppKit/CoreGraphics color conversion; no EventKit store or OS grants."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();functions=source[source.index('// Public EventKit list colors'):source.index('func listRecord(')]
    cases=r'''
import Foundation
import AppKit
'''+functions+r'''
let expected:[String:Any]=["red":0.2,"green":0.4,"blue":0.6,"alpha":1.0]
let color=listColorFromObject(expected)!
let actual=listColorObject(color) as! [String:Double]
for key in expected.keys {precondition(abs(actual[key]! - (expected[key] as! Double))<=0.000001)}
precondition(listColorObject(nil) is NSNull)
for bad in [["red":true,"green":0,"blue":0,"alpha":1], ["red":Double.nan,"green":0,"blue":0,"alpha":1], ["red":Double.infinity,"green":0,"blue":0,"alpha":1], ["red":-0.1,"green":0,"blue":0,"alpha":1], ["red":1.1,"green":0,"blue":0,"alpha":1], ["red":0,"green":0,"blue":0], ["red":0,"green":0,"blue":0,"alpha":1,"other":0]] as [[String:Any]] {precondition(listColorFromObject(bad)==nil)}
let transparent:[String:Any]=["red":0.0,"green":1.0,"blue":0.0,"alpha":0.25]
precondition(listColorFromObject(transparent)==nil)
print("passed")
'''
    with tempfile.TemporaryDirectory(prefix='native-list-colors-') as directory:
        p=Path(directory);(p/'main.swift').write_text(cases)
        result=subprocess.run(['/usr/bin/swiftc',str(p/'main.swift'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr;assert result.stdout.strip()=='passed'
    report={'status':'passed','native_store_access':False,'checks':['explicit_srgb_roundtrip','unsupported_transparency_refused_before_native_setter','unknown_color_is_null','bool_nan_infinite_range_missing_and_extra_fields_refused']};print(json.dumps(report));return report
if __name__=='__main__':run()
