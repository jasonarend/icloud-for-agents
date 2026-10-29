"""Exercise the shipped bounded Vision image processor on synthetic pixels only."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run():
 source=(ROOT/'src/icloud_for_agents/native/notes-ocr.swift').read_text();code=source[source.index('func notesImageText('):source.index('// Fixed bounded stdio entry point')]
 assert 'beginMutation('not in code and 'URL('not in code and 'Application('not in code
 main='''import Foundation
import CoreFoundation
import CryptoKit
import Vision
import ImageIO
import CoreText
import CoreGraphics
'''+code+'''
func pixels(_ width:Int=900)->Data {
 let space=CGColorSpaceCreateDeviceRGB()
 let context=CGContext(data:nil,width:width,height:240,bitsPerComponent:8,bytesPerRow:width*4,space:space,bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue)!
 context.setFillColor(CGColor(gray:1,alpha:1));context.fill(CGRect(x:0,y:0,width:width,height:240))
 let font=CTFontCreateWithName("Helvetica-Bold" as CFString,48,nil)
 let text=NSAttributedString(string:"ICLOUD AGENTS 123",attributes:[NSAttributedString.Key(kCTFontAttributeName as String):font,NSAttributedString.Key(kCTForegroundColorAttributeName as String):CGColor(gray:0,alpha:1)])
 context.textPosition=CGPoint(x:20,y:100);CTLineDraw(CTLineCreateWithAttributedString(text),context)
 let data=NSMutableData();let target=CGImageDestinationCreateWithData(data,"public.png" as CFString,1,nil)!
 CGImageDestinationAddImage(target,context.makeImage()!,nil);precondition(CGImageDestinationFinalize(target));return data as Data
}
DispatchQueue.global(qos:.userInitiated).async {
let image=pixels()
let request:[String:Any] = ["protocol_version":1,"service":"notes","action":"ocr_image","source_ids":["synthetic-source"],"source_id":"synthetic-source","image_data":image.base64EncodedString(),"image_sha256":SHA256.hash(data:image).map({String(format:"%02x",$0)}).joined()]
guard let result=notesImageText(request),let lines=result["lines"] as? [[String:Any]] else {fatalError("Synthetic Vision recognition unavailable")}
let text=lines.compactMap({$0["text"] as? String}).joined(separator:" ").uppercased()
precondition(text.contains("AGENTS") && text.contains("123"))
precondition(result["engine"] as? String == "Apple_Vision" && result["request_revision"] as? Int == 3)
precondition(result["derived"] as? Bool == true && result["native_notes_ocr"] as? Bool == false && result["data_mutated"] as? Bool == false)
precondition(result["truncated"] as? Bool == false && result["size"] as? Int == image.count)
for line in lines {let confidence=line["confidence"] as! Double;precondition(confidence.isFinite && (0...1).contains(confidence))}
for (key,value) in [("protocol_version",true as Any),("service","reminders" as Any),("action","apply" as Any),("source_ids",["other"] as Any),("source_id","" as Any),("image_data","not base64" as Any),("image_sha256",String(repeating:"0",count:64) as Any),("path","/never/read" as Any)] {
 var bad=request;bad[key]=value;precondition(notesImageText(bad)==nil,key)
}
let large=pixels(8193);var oversized=request;oversized["image_data"]=large.base64EncodedString();oversized["image_sha256"]=SHA256.hash(data:large).map({String(format:"%02x",$0)}).joined();precondition(notesImageText(oversized)==nil)
print("passed");exit(0)
}
RunLoop.current.run(until:Date(timeIntervalSinceNow:30))
fatalError("Synthetic OCR read timed out")
'''
 with tempfile.TemporaryDirectory(prefix='native-note-ocr-')as directory:
  path=Path(directory)/'main.swift';path.write_text(main);binary=Path(directory)/'check';built=subprocess.run(['/usr/bin/swiftc',str(path),'-o',str(binary)],capture_output=True,text=True,timeout=90);assert built.returncode==0,built.stderr
  result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=40);assert result.returncode==0 and result.stdout.strip()=='passed',result.stderr
  entry=Path(directory)/'processor';built=subprocess.run(['/usr/bin/swiftc',str(ROOT/'src/icloud_for_agents/native/notes-ocr.swift'),'-o',str(entry)],capture_output=True,text=True,timeout=90);assert built.returncode==0,built.stderr
  for payload in (b'{}',b'x'*240001,b'{\"protocol_version\":true}'):
   refused=subprocess.run([str(entry)],input=payload,capture_output=True,timeout=10);value=json.loads(refused.stdout);assert refused.returncode==0 and value['protocol_version']==1 and value.get('error')in {'invalid_request','read_failed'}
 report={'status':'passed','synthetic_pixels_only':True,'actual_apple_vision_ocr':True,'no_notes_or_reminders_access':True,'identity_hash_format_pixel_and_field_refusals':True,'third_party_runtime_dependencies':[]};out=ROOT/'build/reports/NATIVE-NOTE-OCR-CONTRACT.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':run()
