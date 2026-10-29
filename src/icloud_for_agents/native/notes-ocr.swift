// Fixed local image-byte processor. No Notes authority or native mutations.
import Foundation
import CoreFoundation
import CryptoKit
import Vision
import ImageIO
import Darwin

func notesImageText(_ request:[String:Any])->[String:Any]? {
    let required:Set<String>=["protocol_version","service","action","source_ids","source_id","image_data","image_sha256"]
    guard Set(request.keys)==required || Set(request.keys)==required.union(["request_id"]),
          let version=request["protocol_version"] as? NSNumber,CFGetTypeID(version) != CFBooleanGetTypeID(),version.intValue==1,version.doubleValue==1,
          request["service"] as? String=="notes",request["action"] as? String=="ocr_image",
          let source=request["source_id"] as? String,(1...1000).contains(source.utf8.count),!source.unicodeScalars.contains(where: {CharacterSet.controlCharacters.contains($0)}),
          let selected=request["source_ids"] as? [String],selected==[source],
          let encoded=request["image_data"] as? String,encoded.utf8.count<=174764,
          let imageData=Data(base64Encoded:encoded),!imageData.isEmpty,imageData.count<=131072,imageData.base64EncodedString()==encoded,
          let expected=request["image_sha256"] as? String,expected==SHA256.hash(data:imageData).map({String(format:"%02x",$0)}).joined(),
          let imageSource=CGImageSourceCreateWithData(imageData as CFData,[kCGImageSourceShouldCache:false] as CFDictionary),CGImageSourceGetCount(imageSource)==1,
          let type=CGImageSourceGetType(imageSource) as String?,["public.png","public.jpeg"].contains(type),
          let properties=CGImageSourceCopyPropertiesAtIndex(imageSource,0,nil) as? [CFString:Any],
          let widthNumber=properties[kCGImagePropertyPixelWidth] as? NSNumber,let heightNumber=properties[kCGImagePropertyPixelHeight] as? NSNumber,
          CFGetTypeID(widthNumber) != CFBooleanGetTypeID(),CFGetTypeID(heightNumber) != CFBooleanGetTypeID() else {return nil}
    let width=widthNumber.intValue,height=heightNumber.intValue
    guard widthNumber.doubleValue==Double(width),heightNumber.doubleValue==Double(height),(1...8192).contains(width),(1...8192).contains(height),width*height<=8_000_000 else {return nil}
    let orientationValue=(properties[kCGImagePropertyOrientation] as? NSNumber)?.uint32Value ?? 1
    guard let orientation=CGImagePropertyOrientation(rawValue:orientationValue),
          let image=CGImageSourceCreateImageAtIndex(imageSource,0,[kCGImageSourceShouldCacheImmediately:true] as CFDictionary),image.width==width,image.height==height else {return nil}
    let recognition=VNRecognizeTextRequest()
    recognition.revision=VNRecognizeTextRequestRevision3
    recognition.recognitionLevel = .accurate
    recognition.recognitionLanguages=["en-US"]
    recognition.automaticallyDetectsLanguage=false
    recognition.usesLanguageCorrection=false
    recognition.usesCPUOnly=true
    do {try VNImageRequestHandler(cgImage:image,orientation:orientation,options:[:]).perform([recognition])} catch {return nil}
    guard let observations=recognition.results else {return nil}
    var lines=[[String:Any]](),bytes=0,truncated=false
    for observation in observations {
        if lines.count>=50 {truncated=true;break}
        guard let candidate=observation.topCandidates(1).first else {return nil}
        let text=candidate.string,confidence=Double(candidate.confidence),box=observation.boundingBox
        guard confidence.isFinite,(0...1).contains(confidence),[box.minX,box.minY,box.width,box.height].allSatisfy({$0.isFinite && (0...1).contains($0)}),box.maxX<=1.000001,box.maxY<=1.000001 else {return nil}
        if text.utf8.count>2000 || bytes+text.utf8.count>20000 {truncated=true;break}
        bytes+=text.utf8.count
        lines.append(["text":text,"confidence":confidence,"bounding_box":["x":box.minX,"y":box.minY,"width":box.width,"height":box.height]])
    }
    return ["protocol_version":1,"source_id":source,"image_sha256":expected,"size":imageData.count,"image_type":type,"width":width,"height":height,"orientation":orientationValue,
            "engine":"Apple_Vision","request_revision":3,"language":"en-US","language_correction":false,"lines":lines,"truncated":truncated,
            "derived":true,"native_notes_ocr":false,"data_mutated":false,"order":"Vision_observation_order_not_document_layout"]
}

// Fixed bounded stdio entry point; no app, database, credential or URL access.
let ocrData=FileHandle.standardInput.readData(ofLength:240001)
func emitOCR(_ value:[String:Any])->Never {
 guard let data=try? JSONSerialization.data(withJSONObject:value,options:[.sortedKeys]),data.count<=131072 else {exit(2)}
 FileHandle.standardOutput.write(data);exit(0)
}
guard ocrData.count<=240000,let ocrRequest=(try? JSONSerialization.jsonObject(with:ocrData))as? [String:Any] else {emitOCR(["protocol_version":1,"error":"invalid_request"])}
DispatchQueue.global(qos:.userInitiated).async {
 guard let value=notesImageText(ocrRequest) else {emitOCR(["protocol_version":1,"error":"read_failed"])}
 emitOCR(value)
}
RunLoop.current.run(until:Date(timeIntervalSinceNow:60))
emitOCR(["protocol_version":1,"error":"read_timeout"])
