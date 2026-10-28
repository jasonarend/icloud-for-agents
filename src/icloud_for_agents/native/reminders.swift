// Fixed native protocol: EventKit reads and Notes creation transport.
// The Python approved-plan executor enforces mutation authorization; no implicit permission requests.
import Foundation
import CoreFoundation
import EventKit
import CoreLocation
import OSAKit
import ApplicationServices
import Darwin
import AppKit
import Carbon
import CryptoKit

let ipcDirectory: URL? = {
    guard CommandLine.arguments.count > 1 else { return nil }
    guard CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--ipc-directory" else { exit(2) }
    let url = URL(fileURLWithPath: CommandLine.arguments[2]).standardizedFileURL
    var info = stat()
    guard url.path == url.resolvingSymlinksInPath().path,
          lstat(url.path, &info) == 0, info.st_uid == getuid(),
          info.st_mode & S_IFMT == S_IFDIR, info.st_mode & 0o777 == 0o700 else { exit(2) }
    return url
}()
var responseRequestID: String?
var requestAction: String?
var requestService:String?
var mutationDispatched=false
var privateReminderGateArmed=false
@objc(ICloudAgentsMutationGate)
class NativeMutationGate:NSObject {
    private static let lock=NSLock()
    private static var closed=false
    private static var dispatched=false
    private static var deadline:TimeInterval?
    class func armDeadline() {
        lock.lock();defer {lock.unlock()}
        if deadline == nil {deadline=ProcessInfo.processInfo.systemUptime+25}
    }
    @objc class func beginMutation()->Bool {
        lock.lock();defer {lock.unlock()}
        if let limit=deadline,ProcessInfo.processInfo.systemUptime>=limit {closed=true}
        if closed {return false}
        dispatched=true;return true
    }
    class func cancel()->Bool {
        lock.lock();defer {lock.unlock()}
        closed=true;return dispatched
    }
}
func respond(_ value: [String: Any]) -> Never {
    var envelope = value
    if let id = responseRequestID { envelope["request_id"] = id }
    let data = (try? JSONSerialization.data(withJSONObject: envelope, options: [.sortedKeys])) ?? Data()
    let output = data.count <= 2_000_000 && !data.isEmpty ? data : Data("{\"error\":\"response_limit\",\"protocol_version\":1}".utf8)
    if let directory = ipcDirectory {
        let fd = open(directory.appendingPathComponent("result.json").path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { exit(2) }
        let stream = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        stream.write(output); try? stream.synchronize(); try? stream.close()
    } else { FileHandle.standardOutput.write(output) }
    exit(0)
}
func requestData() -> Data {
    guard let directory = ipcDirectory else { return FileHandle.standardInput.readData(ofLength: 262145) }
    let fd = open(directory.appendingPathComponent("request.json").path, O_RDONLY | O_NOFOLLOW)
    guard fd >= 0 else { exit(2) }
    var info = stat()
    guard fstat(fd, &info) == 0, info.st_uid == getuid(), info.st_mode & S_IFMT == S_IFREG,
          info.st_mode & 0o777 == 0o600, info.st_size <= 262144 else { close(fd); exit(2) }
    let stream = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    return stream.readData(ofLength: 262145)
}
var notesEventSendCount=0
let notesSendWithoutConsent: OSASendUPP = { event,reply,mode,priority,ticks,idle,filter,refCon in
    notesEventSendCount+=1
    let bounded:Int32 = ticks<=0 ? 600 : min(ticks,600)
    return OSErr(truncatingIfNeeded:AESendMessage(event,reply,mode | AESendMode(kAEDoNotPromptForUserConsent),Int(bounded)))
}
func notesScript(_ source:String)->OSAScript? {
    guard let language=OSALanguage(forName:"JavaScript") else { return nil }
    let instance=OSALanguageInstance(language:language)
    guard OSASetSendProc(instance.componentInstance,notesSendWithoutConsent,nil)==noErr else { return nil }
    return OSAScript(source:source,from:nil,languageInstance:instance,using:[])
}
func notesReadiness()->OSStatus {
 let target=NSAppleEventDescriptor(bundleIdentifier:"com.apple.Notes")
 let property=NSAppleEventDescriptor.record()
 property.setDescriptor(NSAppleEventDescriptor(typeCode:typeProperty),forKeyword:AEKeyword(keyAEDesiredClass))
 property.setDescriptor(NSAppleEventDescriptor(enumCode:OSType(formPropertyID)),forKeyword:AEKeyword(keyAEKeyForm))
 property.setDescriptor(NSAppleEventDescriptor(typeCode:AEKeyword(0x76657273)),forKeyword:AEKeyword(keyAEKeyData))
 property.setDescriptor(NSAppleEventDescriptor.null(),forKeyword:AEKeyword(keyAEContainer))
 guard let object=property.coerce(toDescriptorType:typeObjectSpecifier) else {return -1}
 let event=NSAppleEventDescriptor(eventClass:kAECoreSuite,eventID:kAEGetData,targetDescriptor:target,returnID:AEReturnID(kAutoGenerateReturnID),transactionID:AETransactionID(kAnyTransactionID))
 event.setParam(object,forKeyword:keyDirectObject)
 var reply=AppleEvent(descriptorType:typeNull,dataHandle:nil)
 defer {AEDisposeDesc(&reply)}
 notesEventSendCount+=1
 let code=AESendMessage(event.aeDesc,&reply,AESendMode(kAEWaitReply|kAEDoNotPromptForUserConsent),300)
 if code != noErr {return code}
 var remote:Int32=0;var type:DescType=0;var size=0
 let error=AEGetParamPtr(&reply,keyErrorNumber,typeSInt32,&type,&remote,MemoryLayout<Int32>.size,&size)
 if error==noErr && remote != 0 {return remote}
 var value=AEDesc(descriptorType:typeNull,dataHandle:nil)
 defer {AEDisposeDesc(&value)}
 return OSStatus(AEGetParamDesc(&reply,keyDirectObject,typeWildCard,&value))
}

func notesPermission(prompt: Bool) -> OSStatus {
    // AppleEvents.h requires this API off the main thread. Pump the main run loop
    // so an ordinary app-launched helper can participate in the system consent UI.
    if ipcDirectory != nil { _ = NSApplication.shared }
    // Use the stable application address used by the Notes bridge. A PID
    // descriptor can report procNotFound while the same Notes process is alive.
    // Check running state without launching Notes or requesting consent.
    guard NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Notes").contains(where: { !$0.isTerminated }) else { return -600 }
    let completion = DispatchSemaphore(value: 0)
    let lock = NSLock()
    var result: OSStatus = -1712
    DispatchQueue.global(qos: .userInitiated).async {
        let code: OSStatus
        let target = NSAppleEventDescriptor(bundleIdentifier: "com.apple.Notes")
        code = AEDeterminePermissionToAutomateTarget(target.aeDesc, typeWildCard, typeWildCard, prompt)
        lock.lock(); result = code; lock.unlock()
        completion.signal()
    }
    let deadline = Date(timeIntervalSinceNow: prompt ? 60 : 5)
    while Date() < deadline {
        if completion.wait(timeout: .now()) == .success {
            lock.lock(); let code = result; lock.unlock(); return code
        }
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.02))
    }
    return -1712
}
// This identifies this process, not the name shown by the macOS consent dialog.
func notesRequesterIdentity()->[String:Any] {
    return ["bundle_identifier":Bundle.main.bundleIdentifier ?? "unavailable",
            "bundle_name":Bundle.main.object(forInfoDictionaryKey:"CFBundleName") as? String ?? "unavailable",
            "process_name":ProcessInfo.processInfo.processName,
            "launch_mode":ipcDirectory == nil ? "direct_executable" : "application_exchange",
            "dialog_attribution":"not_verified"]
}
func fail(_ code: String) -> Never {
    var value:[String:Any]=["protocol_version":1,"error":code]
    if requestAction=="apply" {
        // EventKit save/deadline failures stay uncertain. Notes atomically
        // closes its shared gate before deciding whether any primitive began.
        if requestService=="notes" {
            let dispatched=NativeMutationGate.cancel()
            value["mutation_dispatched"]=dispatched
            if code=="mutation_failed_or_uncertain" && !dispatched {
                value["error"]="notes_validation_timeout";value["stage"]="validation_before_dispatch"
            }
        } else if privateReminderGateArmed {value["mutation_dispatched"]=NativeMutationGate.cancel()}
        else {value["mutation_dispatched"]=code=="mutation_failed_or_uncertain" || mutationDispatched}
    }
    respond(value)
}
func dateParts(_ date: DateComponents?) -> Any {
    guard let d = date else { return NSNull() }
    var value: [String: Any] = [:]
    for (key, item) in [("year", d.year), ("month", d.month), ("day", d.day), ("hour", d.hour), ("minute", d.minute), ("second", d.second)] {
        if let item = item { value[key] = item }
    }
    value["date_only"] = d.hour == nil && d.minute == nil
    value["timezone"] = d.timeZone?.identifier ?? NSNull() as Any
    return value
}
func notesModelRoleMatches(_ role:[String:Any], folder:String, source:String)->Bool {
    guard let version=role["protocol_version"] as? NSNumber, CFGetTypeID(version) != CFBooleanGetTypeID(), version == 1,
          role["folder_id"] as? String == folder, role["source_id"] as? String == source,
          role["role"] as? String == "ordinary_non_system",
          let mutated=role["data_mutated"] as? NSNumber, CFGetTypeID(mutated) == CFBooleanGetTypeID(), mutated.boolValue == false,
          role["backend"] as? String == "notes-shared-folder-role-v1",
          let number=role["ancestor_count"] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
          let ancestors=role["ancestor_count"] as? Int, (0..<32).contains(ancestors) else {return false}
    return true
}
let data = requestData()
guard data.count <= 262144, let req = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
      req["protocol_version"] as? Int == 1, let action = req["action"] as? String else { fail("invalid_request") }
responseRequestID = req["request_id"] as? String
requestAction=action
guard let service=req["service"] as? String, ["notes","reminders"].contains(service) else { fail("unsupported_service") }
requestService=service
if service == "notes" {
    guard ["status", "store_status", "request_access", "sources", "folders", "default_folder", "folder_get", "folder_inventory", "model_folder_role", "query", "get", "text_page", "search", "attachments", "diagnose_note", "contains", "apply", "diagnose_read_access"].contains(action) else { fail("unsupported_action") }
    if action == "store_status" {
        let output=ICloudNotesStoreStatus()
        guard let value=(try? JSONSerialization.jsonObject(with:output)) as? [String:Any] else {fail("notes_store_status_unavailable")}
        respond(value)
    }
    if action == "model_folder_role" {
        _ = NSApplication.shared
        DispatchQueue.global(qos:.userInitiated).async {
            let output=ICloudNotesModelFolderRole(data)
            guard let value=(try? JSONSerialization.jsonObject(with:output)) as? [String:Any] else {fail("notes_model_invalid_response")}
            respond(value)
        }
        RunLoop.current.run(until:Date(timeIntervalSinceNow:30))
        fail("read_timeout")
    }
    // Reuse the running fixed target without a redundant reopen Apple event.
    // Only the normal helper application launches Notes; permission enforcement
    // remains on every subsequent event from this same host.
    if !["status","store_status"].contains(action) && !NSRunningApplication.runningApplications(withBundleIdentifier:"com.apple.Notes").contains(where:{!$0.isTerminated}) {
        _ = NSApplication.shared
        let configuration=NSWorkspace.OpenConfiguration()
        configuration.activates=false
        configuration.addsToRecentItems=false
        let launchLock=NSLock()
        var finished=false
        var launched=false
        NSWorkspace.shared.openApplication(at:URL(fileURLWithPath:"/System/Applications/Notes.app"),configuration:configuration) {app,error in
            launchLock.lock();defer {launchLock.unlock()}
            launched=app != nil && error == nil
            finished=true
        }
        let deadline=Date(timeIntervalSinceNow:8)
        while Date()<deadline {
            launchLock.lock();let done=finished;launchLock.unlock()
            if done {break}
            _ = RunLoop.current.run(mode:.default,before:Date(timeIntervalSinceNow:0.05))
        }
        launchLock.lock();let ready=finished && launched;launchLock.unlock()
        guard ready else {fail("notes_application_launch_failed")}
    }
    if action == "apply" {NativeMutationGate.armDeadline()}
    if action == "diagnose_read_access" {
        // Explicit operator setup probe: a real, minimal read through normal OS
        // enforcement. Does not inspect note bodies, alter policy or approve consent.
        _ = NSApplication.shared
        DispatchQueue.global(qos: .userInitiated).async {
            let source = "JSON.stringify({account_count:Application('com.apple.Notes').accounts().length})"
            guard let script = notesScript(source) else { fail("notes_bridge_unavailable") }
            var error: NSDictionary?
            if let text = script.executeAndReturnError(&error)?.stringValue,
               let value = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] {
                respond(["protocol_version":1,"read_probe_succeeded":true,"account_count":value["account_count"] ?? 0,
                         "application_writes_enabled":false,"nonprompting_event_count":notesEventSendCount])
            }
            respond(["protocol_version":1,"read_probe_succeeded":false,
                     "error_code":error?[OSAScriptErrorNumberKey] as? Int ?? -1,"application_writes_enabled":false])
        }
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 60))
        fail("read_probe_timeout")
    }
    if action == "status" {
        guard NSRunningApplication.runningApplications(withBundleIdentifier:"com.apple.Notes").contains(where: { !$0.isTerminated }) else {
            respond(["protocol_version":1,"service":"notes","permission_status":-600,"permission_state":"target_not_running","permission_granted":false,"application_writes_enabled":false,"requester_identity":notesRequesterIdentity()])
        }
        // A fixed application-version read proves actual event access without
        // reading note/account content or using the blocking preflight API.
        _ = NSApplication.shared
        DispatchQueue.global(qos:.userInitiated).async {
            let code=notesReadiness()
            let state = code == noErr ? "granted" : code == -1743 ? "denied" : code == -1744 ? "requires_consent" : code == -600 ? "target_not_running" : "unknown"
            respond(["protocol_version":1,"service":"notes","permission_status":code,"permission_state":state,"permission_granted":code==noErr,
                     "application_writes_enabled":false,"nonprompting_event_count":notesEventSendCount,"permission_method":"nonprompting_application_metadata_event","requester_identity":notesRequesterIdentity()])
        }
        RunLoop.current.run(until:Date(timeIntervalSinceNow:8))
        respond(["protocol_version":1,"service":"notes","permission_status":-1712,"permission_state":"unknown_timeout","permission_granted":false,"application_writes_enabled":false,"requester_identity":notesRequesterIdentity()])
    }
    if action == "request_access" {
        let result = notesPermission(prompt: true)
        let state = result == noErr ? "granted" : result == -1743 ? "denied" : result == -1744 ? "requires_consent" : result == -600 ? "target_not_running" : result == -1712 ? "unknown_timeout" : "unknown"
        respond(["protocol_version": 1, "service": "notes", "permission_status": result, "permission_state": state,
                 "permission_granted": result == noErr, "application_writes_enabled": false, "requester_identity":notesRequesterIdentity()])
    }
    if action == "apply", let change=req["change"] as? [String:Any], change["operation"] as? String == "rename_folder", change["writer_backend"] as? String == "notes-shared" {
        guard let url=Bundle.main.url(forResource:"notes-write",withExtension:"js"),let source=try? String(contentsOf:url,encoding:.utf8) else {fail("notes_bridge_unavailable")}
        _ = NSApplication.shared
        _ = NativeMutationGate()
        DispatchQueue.global(qos:.userInitiated).async {
            func scriptingPhase(_ phase:String)->[String:Any]? {
                let encoded=data.base64EncodedString()
                let bridge="\nfunction run(){return applyNotes(Application('com.apple.Notes'),JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding($.NSData.alloc.initWithBase64EncodedStringOptions('"+encoded+"',0),$.NSUTF8StringEncoding))),function(){return Boolean($.NSClassFromString('ICloudAgentsMutationGate').beginMutation);},'"+phase+"');}"
                guard let script=notesScript(source+bridge) else {return nil}
                var diagnostic:NSDictionary?
                guard let output=script.executeAndReturnError(&diagnostic)?.stringValue else {return nil}
                return (try? JSONSerialization.jsonObject(with:Data(output.utf8))) as? [String:Any]
            }
            guard let checked=scriptingPhase("validate_model"),checked["validated"] as? Bool == true else {fail("notes_model_snapshot_refused")}
            let output=ICloudNotesModelRename(data,{NativeMutationGate.beginMutation()})
            guard let value=(try? JSONSerialization.jsonObject(with:output)) as? [String:Any] else {fail("notes_model_invalid_response")}
            if value["result"] != nil {
                // Preserve the saved known ID even if refreshing the app cache fails.
                // Independent scripting + read-only store verification decides success.
                _ = scriptingPhase("refresh_model")
            }
            respond(value)
        }
        RunLoop.current.run(until:Date(timeIntervalSinceNow:30))
        fail("mutation_failed_or_uncertain")
    }
    // Every actual event uses the fixed nonprompting sender below. macOS checks
    // current permission on each send; no cached grant or preflight fallback.
    guard let url = Bundle.main.url(forResource: action == "apply" ? "notes-write" : "notes", withExtension: "js"),
          let source = try? String(contentsOf: url, encoding: .utf8) else { fail("notes_bridge_unavailable") }
    // Only base64 request data is inserted; caller text is never executable source.
    let encoded = data.base64EncodedString()
    let entry = action == "apply" ? "applyNotes" : "readNotes"
    let gate = action == "apply" ? ",function(){return Boolean($.NSClassFromString('ICloudAgentsMutationGate').beginMutation);}" : ""
    let bridge = "\nfunction run() { return " + entry + "(Application('com.apple.Notes'), JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding($.NSData.alloc.initWithBase64EncodedStringOptions('" + encoded + "',0), $.NSUTF8StringEncoding)))" + gate + "); }"
    // Keep the application run loop available while OSAKit sends Apple events.
    // A stalled script must not leave an indefinitely running helper behind.
    _ = NSApplication.shared
    if action=="apply" {_ = NativeMutationGate()}
    DispatchQueue.global(qos: .userInitiated).async {
        if action == "apply", let change=req["change"] as? [String:Any],
           change["folder_guard"] as? String == "verified_model" {
            let folder = (change["operation"] as? String == "create_folder") ? change["parent_folder_id"] as? String : change["folder_id"] as? String
            let source=change["source_id"] as? String
            guard let folder, let source, let sources=req["source_ids"] as? [String], sources.contains(source),
                  let probe=try? JSONSerialization.data(withJSONObject:["folder_id":folder,"source_ids":[source]]),
                  let role=(try? JSONSerialization.jsonObject(with:ICloudNotesModelFolderRole(probe))) as? [String:Any],
                  notesModelRoleMatches(role,folder:folder,source:source) else {
                respond(["protocol_version":1,"error":"notes_model_role_refused","category":"unverified_folder_role","mutation_dispatched":false])
            }
        }
        guard let script = notesScript(source + bridge) else { fail("notes_bridge_unavailable") }
        var diagnostic: NSDictionary?
        guard let output = script.executeAndReturnError(&diagnostic)?.stringValue,
              let value = (try? JSONSerialization.jsonObject(with: Data(output.utf8))) as? [String: Any] else {
            let code = diagnostic?[OSAScriptErrorNumberKey] as? Int ?? -1
            if code == -1743 || code == -1744 { fail("permission_required") }
            var failure:[String:Any]=["protocol_version":1,"error":"notes_operation_failed","error_code":code]
            if action=="apply" {failure["mutation_dispatched"]=NativeMutationGate.cancel()}
            respond(failure)
        }
        var result=value;result["nonprompting_event_count"]=notesEventSendCount
        respond(result)
    }
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 30))
    fail(action == "apply" ? "mutation_failed_or_uncertain" : "notes_read_timeout")
}
let status = EKEventStore.authorizationStatus(for: .reminder)
let permission: String
switch status {
case .notDetermined: permission = "not_determined"
case .restricted: permission = "restricted"
case .denied: permission = "denied"
case .fullAccess: permission = "full_access"
case .writeOnly: permission = "write_only"
@unknown default: permission = "unknown"
}
responseRequestID = req["request_id"] as? String
// This operator-only action is deliberately absent from the MCP read bridge.
if action == "request_access" {
    let store = EKEventStore()
    store.requestFullAccessToReminders { granted, error in
        respond(["protocol_version": 1, "granted": granted, "permission_request": true,
                 "error_present": error != nil, "application_writes_enabled": false])
    }
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 60))
    fail("permission_timeout")
}
let actions = ["status", "sources", "lists", "default_list", "list_sections", "list_features", "list_parent", "list_hierarchy", "group_lists", "templates", "template_sections", "smart_lists", "query", "get", "list_membership", "recovery_lookup", "recovery_tree", "recovery_list", "apply"]
guard actions.contains(action) else { fail("unsupported_action") }
if action == "status" {
    respond(["protocol_version": 1, "service": "reminders", "access": permission,
             "read_only": true, "requests_permissions": false])
}
guard status == .fullAccess else { fail("permission_required") }
let store = EKEventStore()
let allLists = store.calendars(for: .reminder)
if action == "sources" {
    let sources = store.sources.map { source -> [String: Any] in
        ["id": source.sourceIdentifier, "title": source.title, "type": source.sourceType.rawValue,
         "reminder_list_count": allLists.filter { $0.source.sourceIdentifier == source.sourceIdentifier }.count]
    }
    respond(["protocol_version": 1, "sources": sources])
}
guard let sourceIDs = req["source_ids"] as? [String], !sourceIDs.isEmpty, sourceIDs.count <= 100 else { fail("source_scope_required") }
guard sourceIDs.allSatisfy({ id in store.sources.contains(where: { $0.sourceIdentifier == id }) }) else { fail("not_found_in_scope") }
let lists = allLists.filter { sourceIDs.contains($0.source.sourceIdentifier) }
if action == "group_lists" {
    guard let source=req["source_id"] as? String,sourceIDs.contains(source),let id=req["group_id"] as? String,let limit=req["limit"] as? Int,let offset=req["offset"] as? Int,
          let payload=try? JSONSerialization.data(withJSONObject:["source_id":source,"group_id":id,"limit":limit,"offset":offset]),
          let result=(try? JSONSerialization.jsonObject(with:ICloudRemindersGroupLists(payload))) as? [String:Any] else {fail("invalid_request")}
    respond(result)
}
if action == "template_sections" {
    guard let source=req["source_id"] as? String,sourceIDs.contains(source),let id=req["id"] as? String,
          let payload=try? JSONSerialization.data(withJSONObject:["source_id":source,"id":id]),
          let result=(try? JSONSerialization.jsonObject(with:ICloudRemindersTemplateSections(payload))) as? [String:Any] else {fail("invalid_request")}
    respond(result)
}
if action == "smart_lists" {
    guard let source=req["source_id"] as? String,sourceIDs.contains(source),let limit=req["limit"] as? Int,let offset=req["offset"] as? Int,
          let payload=try? JSONSerialization.data(withJSONObject:["source_id":source,"limit":limit,"offset":offset]),
          let result=(try? JSONSerialization.jsonObject(with:ICloudRemindersListSmartLists(payload))) as? [String:Any] else {fail("invalid_request")}
    respond(result)
}
if action == "templates" {
    guard let source=req["source_id"] as? String,sourceIDs.contains(source),let limit=req["limit"] as? Int,let offset=req["offset"] as? Int,
          let payload=try? JSONSerialization.data(withJSONObject:["source_id":source,"limit":limit,"offset":offset]),
          let result=(try? JSONSerialization.jsonObject(with:ICloudRemindersListTemplates(payload))) as? [String:Any] else {fail("invalid_request")}
    respond(result)
}
if action == "recovery_list" {
    guard let source=req["source_id"] as? String,sourceIDs.contains(source),let limit=req["limit"] as? Int,let offset=req["offset"] as? Int,
          let payload=try? JSONSerialization.data(withJSONObject:["source_id":source,"limit":limit,"offset":offset]),
          let result=(try? JSONSerialization.jsonObject(with:ICloudRemindersRecoveryList(payload))) as? [String:Any] else {fail("invalid_request")}
    respond(result)
}
if action == "recovery_lookup" || action == "recovery_tree" {
    guard let id=req["id"] as? String,let source=req["source_id"] as? String,sourceIDs.contains(source),
          let payload=try? JSONSerialization.data(withJSONObject:["id":id,"source_id":source]),
          let result=(try? JSONSerialization.jsonObject(with:action == "recovery_tree" ? ICloudRemindersRecoveryTree(payload) : ICloudRemindersRecoveryLookup(payload))) as? [String:Any] else {fail("invalid_request")}
    respond(result)
}
// Public EventKit list colors are projected into an explicit sRGB space.
func listColorObject(_ color:CGColor?)->Any {
    guard let color=color,let normalized=NSColor(cgColor:color)?.usingColorSpace(.sRGB) else {return NSNull()}
    let values=[normalized.redComponent,normalized.greenComponent,normalized.blueComponent,normalized.alphaComponent]
    guard values.allSatisfy({$0.isFinite && $0>=0 && $0<=1}) else {return NSNull()}
    return Dictionary(uniqueKeysWithValues:zip(["red","green","blue","alpha"],values.map{Double($0)}))
}
func listColorFromObject(_ value:Any?)->CGColor? {
    guard let fields=value as? [String:Any],Set(fields.keys)==["red","green","blue","alpha"] else {return nil}
    var values=[CGFloat]()
    for key in ["red","green","blue","alpha"] {
        guard let number=fields[key] as? NSNumber,CFGetTypeID(number) != CFBooleanGetTypeID(),number.doubleValue.isFinite,(0...1).contains(number.doubleValue) else {return nil}
        values.append(CGFloat(number.doubleValue))
    }
    guard values[3]==1,let space=CGColorSpace(name:CGColorSpace.sRGB) else {return nil}
    return CGColor(colorSpace:space,components:values)
}
func listRecord(_ list:EKCalendar)->[String:Any] {
    ["id":list.calendarIdentifier,"title":String(list.title.prefix(1000)),"source_id":list.source.sourceIdentifier,"writable":list.allowsContentModifications,"color":listColorObject(list.cgColor)]
}
func sectionListFeaturesMatch(_ observed:[String:Any],_ expected:[String:Any])->Bool {
    guard NSDictionary(dictionary:observed).isEqual(to:expected) else {return false}
    for key in ["grocery_categorization","grocery_shopping_session","is_group"] {
        guard let value=observed[key] as? NSNumber,CFGetTypeID(value)==CFBooleanGetTypeID(),!value.boolValue else {return false}
    }
    return true
}
func defaultListRecord(_ candidate:EKCalendar?,_ selected:[EKCalendar])->[String:Any] {
    guard let candidate=candidate else {return ["protocol_version":1,"state":"unavailable","list":NSNull()]}
    guard let list=selected.first(where:{$0.calendarIdentifier==candidate.calendarIdentifier && $0.source.sourceIdentifier==candidate.source.sourceIdentifier}) else {
        return ["protocol_version":1,"state":"outside_selected_scope","list":NSNull()]
    }
    return ["protocol_version":1,"state":"available","list":listRecord(list)]
}
func membershipRecord(_ values:[EKReminder],_ list:EKCalendar)->[String:Any] {
    let ids=values.map { $0.calendarItemIdentifier }.sorted()
    let data=(try? JSONSerialization.data(withJSONObject:ids,options:[.withoutEscapingSlashes])) ?? Data()
    return ["list_id":list.calendarIdentifier,"source_id":list.source.sourceIdentifier,"count":ids.count,
            "sha256":SHA256.hash(data:data).map {String(format:"%02x",$0)}.joined()]
}
if action == "default_list" {respond(defaultListRecord(store.defaultCalendarForNewReminders(),lists))}
if action == "list_hierarchy" {
    guard let id=req["list_id"] as? String,let list=lists.first(where:{$0.calendarIdentifier==id}),
          let payload=try? JSONSerialization.data(withJSONObject:["source_id":list.source.sourceIdentifier,"list_id":id]),
          let native=(try? JSONSerialization.jsonObject(with:ICloudRemindersListHierarchy(payload))) as? [String:Any] else {fail("not_found_in_scope")}
    if native["error"] != nil {respond(native)}
    guard let nodes=native["nodes"] as? [[String:Any]],nodes.count<=200 else {fail("unsupported_list_hierarchy")}
    store.fetchReminders(matching:store.predicateForReminders(in:[list])) { values in
        guard let values=values,values.count<=200,values.count==nodes.count else {fail("hierarchy_membership_disagrees")}
        var publicItems:[String:EKReminder]=[:]
        for value in values {
            guard value.calendar.calendarIdentifier==id,value.calendar.source.sourceIdentifier==list.source.sourceIdentifier,
                  let identifier=UUID(uuidString:value.calendarItemIdentifier)?.uuidString,publicItems[identifier]==nil,
                  let title=value.title,title.count<=1000,title.utf8.count<=4000 else {fail("hierarchy_membership_disagrees")}
            publicItems[identifier]=value
        }
        let nativeIDs=nodes.compactMap{$0["id"] as? String}
        guard nativeIDs.count==nodes.count,Set(nativeIDs)==Set(publicItems.keys) else {fail("hierarchy_membership_disagrees")}
        var result=native
        result["nodes"]=nodes.map { node -> [String:Any] in
            var result=node;let value=publicItems[node["id"] as! String]!;result["title"]=value.title;result["completed"]=value.isCompleted;return result
        }
        result["membership_verified"]=true;result["hierarchy_complete"]=true;result["observation_consistency"]="separate_provider_reads";result["coverage"]="native_list_and_subtasks_matching_eventkit";respond(result)
    }
    RunLoop.current.run(until:Date(timeIntervalSinceNow:30));fail("query_timeout")
}
if action == "list_sections" || action == "list_features" || action == "list_parent" {
    guard let id=req["list_id"] as? String,let list=lists.first(where:{$0.calendarIdentifier==id}) else {fail("not_found_in_scope")}
    var identity:[String:Any]=["source_id":list.source.sourceIdentifier,"list_id":id]
    if let pinned=req["include_pinned"] {
        guard action=="list_features",let value=pinned as? NSNumber,CFGetTypeID(value)==CFBooleanGetTypeID(),value.boolValue else {fail("invalid_request")}
        identity["include_pinned"]=true
    }
    guard let payload=try? JSONSerialization.data(withJSONObject:identity),
          let result=(try? JSONSerialization.jsonObject(with:action == "list_parent" ? ICloudRemindersReadListParent(payload) : action == "list_sections" ? ICloudRemindersListSections(payload) : ICloudRemindersReadListFeatures(payload))) as? [String:Any] else {fail("not_found_in_scope")}
    respond(result)
}
if action == "list_membership" {
    guard let id=req["list_id"] as? String,let list=lists.first(where:{$0.calendarIdentifier==id}) else {fail("not_found_in_scope")}
    store.fetchReminders(matching:store.predicateForReminders(in:[list])) { values in
        guard let values=values,values.count<=100000 else {fail("query_limit")}
        var result=membershipRecord(values,list);result["protocol_version"]=1;respond(result)
    }
    RunLoop.current.run(until:Date(timeIntervalSinceNow:30));fail("read_timeout")
}
// Passive metadata only: storing a link never opens it or executes a handler.
func reminderLink(_ value:Any)->URL? {
    if value is NSNull {return nil}
    guard let text=value as? String,!text.isEmpty,text.count<=4000,
          text.unicodeScalars.allSatisfy({$0.value>=33 && $0.value<127}),!text.contains("\\"),
          let parts=URLComponents(string:text),let scheme=parts.scheme,
          ["http","https","applenotes","x-apple-reminderkit"].contains(scheme),
          parts.user==nil,parts.password==nil,
          let result=parts.url,result.absoluteString==text else {fail("invalid_fields")}
    if scheme=="http" || scheme=="https" {guard let host=parts.host,!host.isEmpty,(parts.port == nil || (1...65535).contains(parts.port!)) else {fail("invalid_fields")}}
    else if (parts.host ?? "").isEmpty && parts.path.isEmpty {fail("invalid_fields")}
    return result
}
func ordinaryTimeAlarm(_ alarm:EKAlarm)->Bool {
    alarm.type == .display && alarm.proximity == .none && alarm.structuredLocation == nil && alarm.emailAddress == nil && alarm.soundName == nil
}
func supportedExistingAlarm(_ alarm:EKAlarm)->Bool {
    if ordinaryTimeAlarm(alarm) {return true}
    guard alarm.type == .display,alarm.emailAddress == nil,alarm.soundName == nil,
          alarm.absoluteDate == nil,alarm.relativeOffset == 0,[EKAlarmProximity.enter,.leave].contains(alarm.proximity),
          let place=alarm.structuredLocation,let title=place.title,!title.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty,
          title.count<=200,title.rangeOfCharacter(from:.controlCharacters)==nil,let geo=place.geoLocation,
          geo.coordinate.latitude.isFinite,(-90...90).contains(geo.coordinate.latitude),geo.coordinate.longitude.isFinite,(-180...180).contains(geo.coordinate.longitude),
          place.radius.isFinite,(0...100000).contains(place.radius) else {return false}
    return true
}
func reminderTimeAlarms(_ value:Any,due:DateComponents?)->[EKAlarm] {
    guard let entries=value as? [[String:Any]],entries.count<=10 else {fail("invalid_fields")}
    var seen=Set<String>();var result:[EKAlarm]=[]
    for entry in entries {
        guard entry.count==1,let key=entry.keys.first else {fail("invalid_fields")}
        if key=="location" {
            guard let value=entry[key] as? [String:Any],Set(value.keys)==["title","latitude","longitude","radius_meters","proximity"],
                  let title=value["title"] as? String,!title.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty,title.count<=200,
                  title.rangeOfCharacter(from:.controlCharacters)==nil,
                  let lat=value["latitude"] as? NSNumber,CFGetTypeID(lat) != CFBooleanGetTypeID(),lat.doubleValue.isFinite,(-90...90).contains(lat.doubleValue),
                  let lon=value["longitude"] as? NSNumber,CFGetTypeID(lon) != CFBooleanGetTypeID(),lon.doubleValue.isFinite,(-180...180).contains(lon.doubleValue),
                  let radius=value["radius_meters"] as? NSNumber,CFGetTypeID(radius) != CFBooleanGetTypeID(),radius.doubleValue.isFinite,(0...100000).contains(radius.doubleValue),
                  let direction=value["proximity"] as? String,["enter","leave"].contains(direction) else {fail("invalid_fields")}
            let identity="location:"+title+":"+lat.stringValue+":"+lon.stringValue+":"+radius.stringValue+":"+direction
            guard !seen.contains(identity) else {fail("invalid_fields")};seen.insert(identity)
            let place=EKStructuredLocation(title:title);place.geoLocation=CLLocation(latitude:lat.doubleValue,longitude:lon.doubleValue);place.radius=radius.doubleValue
            let alarm=EKAlarm(relativeOffset:0);alarm.structuredLocation=place;alarm.proximity=direction=="enter" ? .enter : .leave
            result.append(alarm);continue
        }
        guard ["absolute_date","relative_offset_seconds"].contains(key),
              let number=entry[key] as? NSNumber,CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite,number.doubleValue.rounded()==number.doubleValue else {fail("invalid_fields")}
        let seconds=number.doubleValue;let identity=key+":"+number.stringValue
        guard !seen.contains(identity) else {fail("invalid_fields")};seen.insert(identity)
        if key=="absolute_date" {
            guard seconds>=946684800 && seconds<=4102444799 else {fail("invalid_fields")}
            result.append(EKAlarm(absoluteDate:Date(timeIntervalSince1970:seconds)))
        } else {
            guard abs(seconds)<=31536000,due?.hour != nil,due?.timeZone != nil else {fail("invalid_fields")}
            result.append(EKAlarm(relativeOffset:seconds))
        }
    }
    return result
}
// Gregorian recurrence configuration; never an occurrence expansion or completion.
func recurrenceInteger(_ value:Any?,_ low:Int,_ high:Int,nonzero:Bool=false)->Int {
    guard let number=value as? NSNumber,CFGetTypeID(number) != CFBooleanGetTypeID(),number.doubleValue.isFinite,
          number.doubleValue>=Double(low),number.doubleValue<=Double(high),number.doubleValue.rounded()==number.doubleValue else {fail("invalid_fields")}
    let result=number.intValue;if nonzero && result==0 {fail("invalid_fields")};return result
}
func reminderRecurrence(_ value:Any,due:DateComponents?)->EKRecurrenceRule? {
    if value is NSNull {return nil}
    let arrays=["days_of_month":31,"months_of_year":12,"weeks_of_year":53,"days_of_year":366,"set_positions":366]
    guard let fields=value as? [String:Any],Set(fields.keys)==Set(["frequency","interval","end","days_of_week"]+Array(arrays.keys)),
          let name=fields["frequency"] as? String,let frequency=["daily":EKRecurrenceFrequency.daily,"weekly":.weekly,"monthly":.monthly,"yearly":.yearly][name],
          due?.year != nil,due?.month != nil,due?.day != nil else {fail("invalid_fields")}
    let interval=recurrenceInteger(fields["interval"],1,999)
    var filters:[String:[NSNumber]]=[:]
    for (key,maximum) in arrays {
        guard let values=fields[key] as? [Any],values.count<=366 else {fail("invalid_fields")}
        let ints=values.map {recurrenceInteger($0,key=="months_of_year" ? 1 : -maximum,maximum,nonzero:true)}
        guard Set(ints).count==ints.count,ints==ints.sorted() else {fail("invalid_fields")}
        if !ints.isEmpty && ((key=="days_of_month" && name != "monthly") || (["months_of_year","weeks_of_year","days_of_year"].contains(key) && name != "yearly")) {fail("invalid_fields")}
        filters[key]=ints.map {NSNumber(value:$0)}
    }
    guard let days=fields["days_of_week"] as? [[String:Any]],days.count<=53 else {fail("invalid_fields")}
    var weekdays:[EKRecurrenceDayOfWeek]=[];var seen=Set<String>()
    for day in days {
        guard Set(day.keys)==["weekday","week_number"],name != "daily" else {fail("invalid_fields")}
        let weekday=recurrenceInteger(day["weekday"],1,7),maximum=name=="yearly" ? 53 : 5
        let ordinal=recurrenceInteger(day["week_number"],-maximum,maximum)
        let identity=String(weekday)+":"+String(ordinal)
        guard !seen.contains(identity),name != "weekly" || ordinal==0,let nativeDay=EKWeekday(rawValue:weekday) else {fail("invalid_fields")}
        seen.insert(identity);weekdays.append(EKRecurrenceDayOfWeek(nativeDay,weekNumber:ordinal))
    }
    if !(filters["set_positions"] ?? []).isEmpty && weekdays.isEmpty && !["days_of_month","months_of_year","weeks_of_year","days_of_year"].contains(where:{!(filters[$0] ?? []).isEmpty}) {fail("invalid_fields")}
    var end:EKRecurrenceEnd?=nil
    if !(fields["end"] is NSNull) {
        guard let limit=fields["end"] as? [String:Any],limit.count==1 else {fail("invalid_fields")}
        if limit["count"] != nil {end=EKRecurrenceEnd(occurrenceCount:recurrenceInteger(limit["count"],1,10000))}
        else if limit["date"] != nil {
            let date=Date(timeIntervalSince1970:Double(recurrenceInteger(limit["date"],946684800,4102444799)))
            guard let due=due,let zone=due.timeZone,due.hour != nil else {fail("invalid_fields")}
            var calendar=Calendar(identifier:.gregorian);calendar.timeZone=zone
            guard let first=calendar.date(from:due),date>=first else {fail("invalid_fields")}
            end=EKRecurrenceEnd(end:date)
        }
        else {fail("invalid_fields")}
    }
    func numbers(_ key:String)->[NSNumber]? {let values=filters[key] ?? [];return values.isEmpty ? nil : values}
    let rule=EKRecurrenceRule(recurrenceWith:frequency,interval:interval,daysOfTheWeek:weekdays.isEmpty ? nil : weekdays,
        daysOfTheMonth:numbers("days_of_month"),monthsOfTheYear:numbers("months_of_year"),weeksOfTheYear:numbers("weeks_of_year"),
        daysOfTheYear:numbers("days_of_year"),setPositions:numbers("set_positions"),end:end)
    // Refuse combinations Foundation ignores before saving a native object.
    var expected=fields;expected["fields_complete"]=true;expected["calendar_identifier"]="gregorian";expected["first_weekday"]=rule.firstDayOfTheWeek
    guard [0,2].contains(rule.firstDayOfTheWeek) else {fail("unsupported_edit_target")}
    expected["end"]=end.map { ["count":$0.occurrenceCount,"date":$0.endDate?.timeIntervalSince1970 ?? NSNull() as Any] as [String:Any] } ?? NSNull() as Any
    guard NSDictionary(dictionary:recurrenceObject(rule)).isEqual(to:expected) else {fail("unsupported_edit_target")}
    return rule
}
if action == "apply" {
    if let change=req["change"] as? [String:Any],let operation=change["operation"] as? String,["set_flagged","remove_tag","add_tag","replace_tag","set_urgent","add_early","clear_early","create_subtask","create_section","rename_section"].contains(operation) {
        privateReminderGateArmed=true;NativeMutationGate.armDeadline()
    }
    if let item=req["change"] as? [String:Any],let operation=item["operation"] as? String,["create_section","rename_section"].contains(operation) {
        guard let source=item["source_id"] as? String,sourceIDs.contains(source),let listID=item["list_id"] as? String,
              let sectionID=item["section_id"] as? String,let fields=item["fields"] as? [String:Any],Set(fields.keys)==["title"],let title=fields["title"] as? String,!title.isEmpty,
              let before=item["before"] as? [String:Any],let expectedList=before["list"] as? [String:Any],let expectedSiblings=before["siblings"] as? [[String:Any]],
              let expectedMembers=before["membership"] as? [String:Any],let expectedSections=before["sections"] as? [[String:Any]],let expectedFeatures=before["list_features"] as? [String:Any],
              let featurePayload=try? JSONSerialization.data(withJSONObject:["source_id":source,"list_id":listID]),let observedFeatures=(try? JSONSerialization.jsonObject(with:ICloudRemindersReadListFeatures(featurePayload))) as? [String:Any],
              let list=lists.first(where:{$0.calendarIdentifier==listID && $0.source.sourceIdentifier==source}),list.allowsContentModifications else {fail("invalid_mutation")}
        guard sectionListFeaturesMatch(observedFeatures,expectedFeatures) else {fail("snapshot_changed")}
        let siblings=lists.filter{$0.source.sourceIdentifier==source && $0.calendarIdentifier != listID}.sorted{$0.calendarIdentifier<$1.calendarIdentifier}
        guard NSDictionary(dictionary:listRecord(list)).isEqual(to:expectedList),NSArray(array:siblings.map(listRecord)).isEqual(to:expectedSiblings),
              !expectedSections.contains(where:{$0["title"] as? String==title}),
              (operation=="create_section" || expectedSections.filter({$0["id"] as? String==sectionID}).count==1),
              let payload=try? JSONSerialization.data(withJSONObject:["source_id":source,"list_id":listID,"section_id":sectionID,"title":title,"expected_sections":expectedSections]) else {fail("snapshot_changed")}
        store.fetchReminders(matching:store.predicateForReminders(in:[list])) {values in
            guard let values=values,values.count<=100000,NSDictionary(dictionary:membershipRecord(values,list)).isEqual(to:expectedMembers) else {fail("snapshot_changed")}
            guard let response=(try? JSONSerialization.jsonObject(with:operation=="create_section" ? ICloudRemindersCreateSection(payload,{NativeMutationGate.beginMutation()}) : ICloudRemindersRenameSection(payload,{NativeMutationGate.beginMutation()}))) as? [String:Any] else {fail("invalid_mutation")}
            if response["error"] != nil {fail((response["mutation_dispatched"] as? Bool)==true ? "mutation_failed_or_uncertain" : "unsupported_edit_target")}
            guard let output=response["result"] as? [String:Any] else {fail("mutation_failed_or_uncertain")}
            respond(["protocol_version":1,"result":output])
        }
        RunLoop.current.run(until:Date(timeIntervalSinceNow:30));fail("mutation_failed_or_uncertain")
    }
    if let item=req["change"] as? [String:Any],let operation=item["operation"] as? String,["rename_list","set_list_color"].contains(operation) {
        guard let source=item["source_id"] as? String,sourceIDs.contains(source),let id=item["list_id"] as? String,
              let fields=item["fields"] as? [String:Any],
              let before=item["before"] as? [String:Any],let expected=before["list"] as? [String:Any],
              let expectedMembers=before["membership"] as? [String:Any],let expectedSiblings=before["siblings"] as? [[String:Any]],
              let list=lists.first(where:{$0.calendarIdentifier==id && $0.source.sourceIdentifier==source}),list.allowsContentModifications else {fail("invalid_mutation")}
        let title=fields["title"] as? String
        let color=listColorFromObject(fields["color"])
        guard (operation=="rename_list" && Set(fields.keys)==["title"] && title != nil && !title!.isEmpty && title!.count<=1000) || (operation=="set_list_color" && Set(fields.keys)==["color"] && color != nil) else {fail("invalid_fields")}
        let siblings=lists.filter {$0.source.sourceIdentifier==source && $0.calendarIdentifier != id}.sorted {$0.calendarIdentifier<$1.calendarIdentifier}
        guard NSDictionary(dictionary:listRecord(list)).isEqual(to:expected),NSArray(array:siblings.map(listRecord)).isEqual(to:expectedSiblings),
              (operation != "rename_list" || !siblings.contains(where:{$0.title==title})) else {fail("snapshot_changed")}
        store.fetchReminders(matching:store.predicateForReminders(in:[list])) { values in
            guard let values=values,values.count<=100000,NSDictionary(dictionary:membershipRecord(values,list)).isEqual(to:expectedMembers) else {fail("snapshot_changed")}
            if operation=="rename_list" {list.title=title!} else {list.cgColor=color!}
            mutationDispatched=true
            do {try store.saveCalendar(list,commit:true)} catch {fail("mutation_failed_or_uncertain")}
            respond(["protocol_version":1,"result":["id":id,"source_id":source,"saved_locally":true]])
        }
        RunLoop.current.run(until:Date(timeIntervalSinceNow:30));fail("mutation_failed_or_uncertain")
    }
    if let item = req["change"] as? [String: Any], let operation = item["operation"] as? String,
       ["update_reminder", "set_completed", "move_reminder", "set_recurrence", "set_flagged", "remove_tag", "add_tag", "replace_tag", "set_urgent", "add_early", "clear_early", "create_subtask"].contains(operation) {
        if operation == "move_reminder" { fail("unsupported_hierarchy_move") }
        guard let sourceID = item["source_id"] as? String, sourceIDs.contains(sourceID),
              let identifier = item["id"] as? String, let fields = item["fields"] as? [String: Any],
              let before = item["before"] as? [String: Any], let previous = before["item"] as? [String: Any],
              !lists.isEmpty else { fail("invalid_mutation") }
        store.fetchReminders(matching: store.predicateForReminders(in: lists)) { reminders in
            guard let reminders = reminders, reminders.count <= 100000,
                  let reminder = reminders.first(where: { $0.calendarItemIdentifier == identifier }),
                  reminder.calendar.source.sourceIdentifier == sourceID,
                  reminder.calendar.allowsContentModifications else { fail("not_found_in_scope") }
            guard NSDictionary(dictionary: object(reminder)).isEqual(to: previous), (operation=="set_recurrence" || !reminder.hasRecurrenceRules) else {
                fail("snapshot_changed_or_recurrence_unsupported")
            }
            if let selectedNative=before["native_metadata"] {
                guard let expectedNative=selectedNative as? [String:Any] else {fail("invalid_mutation")}
                guard nativeSnapshotMatches(reminder,expectedNative) else {fail("snapshot_changed")}
                guard let prior=before["urgent"] as? NSNumber,CFGetTypeID(prior)==CFBooleanGetTypeID(),
                      let identity=try? JSONSerialization.data(withJSONObject:["id":identifier,"source_id":sourceID,"list_id":reminder.calendar.calendarIdentifier]),
                      let urgentResponse=(try? JSONSerialization.jsonObject(with:ICloudRemindersReadUrgent(identity))) as? [String:Any],
                      let urgentMetadata=urgentResponse["native_metadata"] as? [String:Any],let observed=urgentMetadata["urgent"] as? NSNumber,
                      CFGetTypeID(observed)==CFBooleanGetTypeID(),observed.boolValue==prior.boolValue else {fail("snapshot_changed")}

            }
            if operation == "create_subtask" {
                guard !reminder.isCompleted,Set(fields.keys)==["title"],let title=fields["title"] as? String,!title.isEmpty,let childID=item["child_id"] as? String,
                      let native=before["native_metadata"] as? [String:Any],let children=native["child_ids"] as? [String],
                      let payload=try? JSONSerialization.data(withJSONObject:["id":identifier,"source_id":sourceID,"list_id":reminder.calendar.calendarIdentifier,"child_id":childID,"title":title,"expected_children":children]),
                      let response=(try? JSONSerialization.jsonObject(with:ICloudRemindersCreateSubtask(payload,{NativeMutationGate.beginMutation()}))) as? [String:Any] else {fail("invalid_mutation")}
                if response["error"] != nil {fail((response["mutation_dispatched"] as? Bool)==true ? "mutation_failed_or_uncertain" : "unsupported_edit_target")}
                guard let output=response["result"] as? [String:Any] else {fail("mutation_failed_or_uncertain")}
                respond(["protocol_version":1,"result":output])
            }
            if operation == "clear_early" {
                guard Set(fields.keys)==["clear"],let clear=fields["clear"] as? NSNumber,CFGetTypeID(clear)==CFBooleanGetTypeID(),clear.boolValue,
                      let native=before["native_metadata"] as? [String:Any],let alerts=native["early_alerts"] as? [[String:Any]],alerts.count==1,let fingerprint=native["early_payload_fingerprint"] as? String,fingerprint.count==64,
                      let payload=try? JSONSerialization.data(withJSONObject:["id":identifier,"source_id":sourceID,"list_id":reminder.calendar.calendarIdentifier,"expected_fingerprint":fingerprint]),
                      let response=(try? JSONSerialization.jsonObject(with:ICloudRemindersClearEarly(payload,{NativeMutationGate.beginMutation()}))) as? [String:Any] else {fail("invalid_mutation")}
                if response["error"] != nil {fail((response["mutation_dispatched"] as? Bool)==true ? "mutation_failed_or_uncertain" : "unsupported_edit_target")}
                guard let output=response["result"] as? [String:Any] else {fail("mutation_failed_or_uncertain")}
                respond(["protocol_version":1,"result":output])
            }
            if operation == "add_early" {
                let units:[String:Int] = ["minutes":0,"hours":1,"days":2,"weeks":3,"months":4]
                guard Set(fields.keys)==["unit","count"],let unitName=fields["unit"] as? String,let unit=units[unitName],let count=fields["count"] as? NSNumber,CFGetTypeID(count) != CFBooleanGetTypeID(),count.intValue>=1,count.intValue<=10000,count.doubleValue==Double(count.intValue),let due=reminder.dueDateComponents,due.hour != nil,due.minute != nil,
                      let payload=try? JSONSerialization.data(withJSONObject:["id":identifier,"source_id":sourceID,"list_id":reminder.calendar.calendarIdentifier,"unit":unit,"count": -count.intValue]),
                      let response=(try? JSONSerialization.jsonObject(with:ICloudRemindersAddEarly(payload,{NativeMutationGate.beginMutation()}))) as? [String:Any] else {fail("invalid_mutation")}
                if response["error"] != nil {fail((response["mutation_dispatched"] as? Bool)==true ? "mutation_failed_or_uncertain" : "unsupported_edit_target")}
                guard let output=response["result"] as? [String:Any] else {fail("mutation_failed_or_uncertain")}
                respond(["protocol_version":1,"result":output])
            }
            if operation == "set_urgent" {
                guard Set(fields.keys)==["urgent"],let urgent=fields["urgent"] as? NSNumber,CFGetTypeID(urgent)==CFBooleanGetTypeID(),
                      let prior=before["urgent"] as? NSNumber,CFGetTypeID(prior)==CFBooleanGetTypeID(),
                      let payload=try? JSONSerialization.data(withJSONObject:["id":identifier,"source_id":sourceID,"list_id":reminder.calendar.calendarIdentifier,"urgent":urgent,"expected_urgent":prior]),
                      let response=(try? JSONSerialization.jsonObject(with:ICloudRemindersSetUrgent(payload,{NativeMutationGate.beginMutation()}))) as? [String:Any] else {fail("invalid_mutation")}
                if response["error"] != nil {fail((response["mutation_dispatched"] as? Bool)==true ? "mutation_failed_or_uncertain" : "unsupported_edit_target")}
                guard let output=response["result"] as? [String:Any] else {fail("mutation_failed_or_uncertain")}
                respond(["protocol_version":1,"result":output])
            }
            if operation == "set_flagged" {
                guard Set(fields.keys)==["flagged"],let flag=fields["flagged"] as? NSNumber,CFGetTypeID(flag)==CFBooleanGetTypeID(),
                      let expected=before["native_metadata"] as? [String:Any],let prior=expected["flagged"] as? NSNumber,CFGetTypeID(prior)==CFBooleanGetTypeID(),
                      let payload=try? JSONSerialization.data(withJSONObject:["id":identifier,"source_id":sourceID,"list_id":reminder.calendar.calendarIdentifier,"flagged":flag,"expected_flagged":prior]),
                      let response=(try? JSONSerialization.jsonObject(with:ICloudRemindersSetFlag(payload,{NativeMutationGate.beginMutation()}))) as? [String:Any] else {fail("invalid_mutation")}
                if response["error"] != nil {fail((response["mutation_dispatched"] as? Bool)==true ? "mutation_failed_or_uncertain" : "unsupported_edit_target")}
                guard let output=response["result"] as? [String:Any] else {fail("mutation_failed_or_uncertain")}
                respond(["protocol_version":1,"result":output])
            }
            if operation == "replace_tag" {
                guard Set(fields.keys)==["tag_id","tag_name"],let tag=fields["tag_id"] as? String,let name=fields["tag_name"] as? String,!tag.isEmpty,!name.isEmpty,
                      let expected=before["native_metadata"] as? [String:Any],let tags=expected["tags"] as? [[String:Any]],
                      let payload=try? JSONSerialization.data(withJSONObject:["id":identifier,"source_id":sourceID,"list_id":reminder.calendar.calendarIdentifier,"tag_id":tag,"tag_name":name,"expected_tags":tags]),
                      let response=(try? JSONSerialization.jsonObject(with:ICloudRemindersReplaceTag(payload,{NativeMutationGate.beginMutation()}))) as? [String:Any] else {fail("invalid_mutation")}
                if response["error"] != nil {fail((response["mutation_dispatched"] as? Bool)==true ? "mutation_failed_or_uncertain" : "unsupported_edit_target")}
                guard let output=response["result"] as? [String:Any] else {fail("mutation_failed_or_uncertain")}
                respond(["protocol_version":1,"result":output])
            }
            if operation == "add_tag" {
                guard Set(fields.keys)==["tag_name"],let name=fields["tag_name"] as? String,!name.isEmpty,
                      let expected=before["native_metadata"] as? [String:Any],let tags=expected["tags"] as? [[String:Any]],
                      let payload=try? JSONSerialization.data(withJSONObject:["id":identifier,"source_id":sourceID,"list_id":reminder.calendar.calendarIdentifier,"tag_name":name,"expected_tags":tags]),
                      let response=(try? JSONSerialization.jsonObject(with:ICloudRemindersAddTag(payload,{NativeMutationGate.beginMutation()}))) as? [String:Any] else {fail("invalid_mutation")}
                if response["error"] != nil {fail((response["mutation_dispatched"] as? Bool)==true ? "mutation_failed_or_uncertain" : "unsupported_edit_target")}
                guard let output=response["result"] as? [String:Any] else {fail("mutation_failed_or_uncertain")}
                respond(["protocol_version":1,"result":output])
            }
            if operation == "remove_tag" {
                guard Set(fields.keys)==["tag_id"],let tag=fields["tag_id"] as? String,!tag.isEmpty,
                      let expected=before["native_metadata"] as? [String:Any],let tags=expected["tags"] as? [[String:Any]],
                      let payload=try? JSONSerialization.data(withJSONObject:["id":identifier,"source_id":sourceID,"list_id":reminder.calendar.calendarIdentifier,"tag_id":tag,"expected_tags":tags]),
                      let response=(try? JSONSerialization.jsonObject(with:ICloudRemindersRemoveTag(payload,{NativeMutationGate.beginMutation()}))) as? [String:Any] else {fail("invalid_mutation")}
                if response["error"] != nil {fail((response["mutation_dispatched"] as? Bool)==true ? "mutation_failed_or_uncertain" : "unsupported_edit_target")}
                guard let output=response["result"] as? [String:Any] else {fail("mutation_failed_or_uncertain")}
                respond(["protocol_version":1,"result":output])
            }
            if operation == "set_completed" {
                guard Set(fields.keys) == ["completed"], let completed = fields["completed"] as? Bool else { fail("invalid_fields") }
                reminder.isCompleted = completed
            } else if operation == "set_recurrence" {
                guard Set(fields.keys)==["recurrence","recurrence_scope"],fields["recurrence_scope"] as? String=="exact_native_rule",!reminder.isCompleted,
                      object(reminder)["public_metadata_complete"] as? Bool==true,(reminder.recurrenceRules?.count ?? 0)<=1 else {fail("unsupported_edit_target")}
                if let rule=reminder.recurrenceRules?.first {
                    var metadata=recurrenceObject(rule)
                    guard metadata["calendar_identifier"] as? String=="gregorian" else {fail("unsupported_edit_target")}
                    var fields=metadata;fields.removeValue(forKey:"fields_complete");fields.removeValue(forKey:"calendar_identifier");fields.removeValue(forKey:"first_weekday")
                    if let end=fields["end"] as? [String:Any] {fields["end"]=(end["date"] is NSNull) ? ["count":end["count"]!] : ["date":end["date"]!]}
                    guard let first=metadata["first_weekday"] as? Int,[0,2].contains(first),let represented=reminderRecurrence(fields,due:reminder.dueDateComponents) else {fail("unsupported_edit_target")}
                    metadata["first_weekday"]=represented.firstDayOfTheWeek
                    guard NSDictionary(dictionary:recurrenceObject(represented)).isEqual(to:metadata) else {fail("unsupported_edit_target")}
                }
                let rule=reminderRecurrence(fields["recurrence"]!,due:reminder.dueDateComponents)
                reminder.recurrenceRules=rule.map {[$0]} ?? []
            } else if operation == "move_reminder" {
                guard Set(fields.keys) == ["list_id"], let listID = fields["list_id"] as? String,
                      let destination = lists.first(where: { $0.calendarIdentifier == listID && $0.source.sourceIdentifier == sourceID }),
                      destination.allowsContentModifications else { fail("not_found_in_scope") }
                reminder.calendar = destination
            } else {
                guard !fields.isEmpty, Set(fields.keys).isSubset(of: ["title", "notes", "priority", "due", "start", "url", "alarms"]) else { fail("invalid_fields") }
                if fields["alarms"] != nil {
                    guard (reminder.alarms?.count ?? 0)<=10,(reminder.alarms ?? []).allSatisfy(supportedExistingAlarm) else {fail("unsupported_edit_target")}
                }
                if let title = fields["title"] as? String { reminder.title = title }
                if let notes = fields["notes"] as? String { reminder.notes = notes }
                if let priority = fields["priority"] as? Int { reminder.priority = priority }
                if let value=fields["url"] {reminder.url=reminderLink(value)}
                func patchDate(_ value: Any) -> DateComponents? {
                    guard let data = value as? [String: Any] else { return nil }
                    var result = DateComponents(); result.calendar = Calendar(identifier: .gregorian)
                    result.year = data["year"] as? Int; result.month = data["month"] as? Int; result.day = data["day"] as? Int
                    result.hour = data["hour"] as? Int; result.minute = data["minute"] as? Int
                    if let zone = data["timezone"] as? String { result.timeZone = TimeZone(identifier: zone) }
                    return result
                }
                // EventKit may synthesize start components when due changes.
                // Restore the previously reviewed start unless explicitly patched.
                let previousStart = reminder.startDateComponents
                if let due = fields["due"] { reminder.dueDateComponents = patchDate(due) }
                if let start = fields["start"] { reminder.startDateComponents = patchDate(start) }
                else { reminder.startDateComponents = previousStart }
                if let alarms=fields["alarms"] {reminder.alarms=reminderTimeAlarms(alarms,due:reminder.dueDateComponents)}
            }
            mutationDispatched=true
            do { try store.save(reminder, commit: true) } catch { fail("mutation_failed_or_uncertain") }
            respond(["protocol_version":1,"result":["id":reminder.calendarItemIdentifier,
                "list_id":reminder.calendar.calendarIdentifier,"source_id":sourceID,"saved_locally":true]])
        }
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 30))
        fail("mutation_failed_or_uncertain")
    }
    guard let item = req["change"] as? [String: Any], let operation = item["operation"] as? String,
          let sourceID = item["source_id"] as? String, sourceIDs.contains(sourceID),
          let fields = item["fields"] as? [String: Any], let title = fields["title"] as? String,
          !title.isEmpty, title.count <= 1000 else { fail("invalid_mutation") }
    if operation == "create_list" {
        guard Set(fields.keys)==["title"] else {fail("invalid_fields")}
        guard let source = store.sources.first(where: { $0.sourceIdentifier == sourceID }) else { fail("not_found_in_scope") }
        guard !lists.contains(where: { $0.source.sourceIdentifier == sourceID && $0.title == title }) else { fail("duplicate_list") }
        let list = EKCalendar(for: .reminder, eventStore: store)
        list.source = source; list.title = title
        mutationDispatched=true
        do { try store.saveCalendar(list, commit: true) } catch { fail("mutation_failed_or_uncertain") }
        respond(["protocol_version": 1, "result": ["id": list.calendarIdentifier, "source_id": sourceID, "saved_locally": true]])
    }
    guard operation == "create_reminder", let listID = item["list_id"] as? String,
          let list = lists.first(where: { $0.calendarIdentifier == listID && $0.source.sourceIdentifier == sourceID }),
          list.allowsContentModifications else { fail("not_found_in_scope") }
    func components(_ value: Any?) -> DateComponents? {
        guard let fields = value as? [String: Any] else { return nil }
        var result = DateComponents()
        result.calendar = Calendar(identifier: .gregorian)
        result.year = fields["year"] as? Int; result.month = fields["month"] as? Int; result.day = fields["day"] as? Int
        result.hour = fields["hour"] as? Int; result.minute = fields["minute"] as? Int
        if let timezone = fields["timezone"] as? String { result.timeZone = TimeZone(identifier: timezone) }
        return result
    }
    guard Set(fields.keys).isSubset(of:["title","notes","priority","due","start","url","alarms","recurrence"]) else {fail("invalid_fields")}
    let reminder = EKReminder(eventStore: store)
    reminder.calendar = list; reminder.title = title
    reminder.notes = fields["notes"] as? String ?? ""
    reminder.priority = fields["priority"] as? Int ?? 0
    if let value=fields["url"] {reminder.url=reminderLink(value)}
    reminder.dueDateComponents = components(fields["due"])
    reminder.startDateComponents = components(fields["start"])
    if let alarms=fields["alarms"] {reminder.alarms=reminderTimeAlarms(alarms,due:reminder.dueDateComponents)}
    if let value=fields["recurrence"] {reminder.recurrenceRules=reminderRecurrence(value,due:reminder.dueDateComponents).map {[$0]} ?? []}
    mutationDispatched=true
    do { try store.save(reminder, commit: true) } catch { fail("mutation_failed_or_uncertain") }
    respond(["protocol_version": 1, "result": ["id": reminder.calendarItemIdentifier, "list_id": listID,
             "source_id": sourceID, "saved_locally": true]])
}
if action == "lists" {
    let limit = req["limit"] as? Int ?? 100
    let offset = req["offset"] as? Int ?? 0
    guard (1...100).contains(limit), (0...100000).contains(offset) else { fail("invalid_page") }
    let sorted = lists.sorted { $0.calendarIdentifier < $1.calendarIdentifier }
    let page = Array(sorted.dropFirst(offset).prefix(limit))
    respond(["protocol_version": 1, "lists": page.map(listRecord), "done": offset + page.count >= sorted.count, "next_offset": offset + page.count,
       "pagination_consistency": "live_offset_may_shift"])
}

// Public metadata only; these serializers never fetch contacts or perform alarms.
func recurrenceObject(_ rule: EKRecurrenceRule) -> [String: Any] {
    let frequency: String
    switch rule.frequency {
    case .daily: frequency="daily"
    case .weekly: frequency="weekly"
    case .monthly: frequency="monthly"
    case .yearly: frequency="yearly"
    @unknown default: frequency="unknown"
    }
    let fieldCounts=[rule.daysOfTheWeek?.count ?? 0,rule.daysOfTheMonth?.count ?? 0,rule.daysOfTheYear?.count ?? 0,rule.weeksOfTheYear?.count ?? 0,rule.monthsOfTheYear?.count ?? 0,rule.setPositions?.count ?? 0]
    return ["fields_complete":fieldCounts.allSatisfy { $0 <= 1000 },"frequency":frequency,"interval":rule.interval,"first_weekday":rule.firstDayOfTheWeek,
        "calendar_identifier":rule.calendarIdentifier,
        "days_of_week":(rule.daysOfTheWeek ?? []).prefix(1000).map { ["weekday":$0.dayOfTheWeek.rawValue,"week_number":$0.weekNumber] },
        "days_of_month":Array((rule.daysOfTheMonth ?? []).prefix(1000)),
        "days_of_year":Array((rule.daysOfTheYear ?? []).prefix(1000)),
        "weeks_of_year":Array((rule.weeksOfTheYear ?? []).prefix(1000)),
        "months_of_year":Array((rule.monthsOfTheYear ?? []).prefix(1000)),
        "set_positions":Array((rule.setPositions ?? []).prefix(1000)),
        "end":rule.recurrenceEnd.map { end -> [String: Any] in
            ["count":end.occurrenceCount,"date":end.endDate?.timeIntervalSince1970 ?? NSNull() as Any]
        } ?? NSNull() as Any]
}
func alarmObject(_ alarm: EKAlarm) -> [String: Any] {
    var location: Any = NSNull()
    if let place=alarm.structuredLocation {
        location=["title":place.title ?? "","radius_meters":place.radius,
            "latitude":place.geoLocation?.coordinate.latitude ?? NSNull() as Any,
            "longitude":place.geoLocation?.coordinate.longitude ?? NSNull() as Any] as [String:Any]
    }
    return ["relative_offset_seconds":alarm.relativeOffset,
        "absolute_date":alarm.absoluteDate?.timeIntervalSince1970 ?? NSNull() as Any,
        "proximity":alarm.proximity.rawValue,"type":alarm.type.rawValue,
        "structured_location":location,"email_address":alarm.emailAddress ?? NSNull() as Any,
        "sound_name":alarm.soundName ?? NSNull() as Any]
}
func object(_ reminder: EKReminder) -> [String: Any] {
    ["id": reminder.calendarItemIdentifier, "source_id": reminder.calendar.source.sourceIdentifier,
     "list_id": reminder.calendar.calendarIdentifier, "title": reminder.title ?? "", "notes": reminder.notes ?? "",
     "completed": reminder.isCompleted, "priority": reminder.priority,
     "due": dateParts(reminder.dueDateComponents), "start": dateParts(reminder.startDateComponents),
     "created": reminder.creationDate?.timeIntervalSince1970 ?? NSNull() as Any,
     "modified": reminder.lastModifiedDate?.timeIntervalSince1970 ?? NSNull() as Any,
     "completion_date": reminder.completionDate?.timeIntervalSince1970 ?? NSNull() as Any,
     "url": reminder.url?.absoluteString ?? NSNull() as Any,
     "location": reminder.location ?? NSNull() as Any,
     "has_recurrence": reminder.hasRecurrenceRules, "has_alarms": reminder.hasAlarms,
     "recurrence_rules":(reminder.recurrenceRules ?? []).prefix(100).map(recurrenceObject),
     "alarms":(reminder.alarms ?? []).prefix(100).map(alarmObject),
     "public_metadata_complete":(reminder.recurrenceRules?.count ?? 0)<=100 && (reminder.alarms?.count ?? 0)<=100 && (reminder.recurrenceRules ?? []).allSatisfy { recurrenceObject($0)["fields_complete"] as? Bool == true },
     "content_complete": false, "omitted_features": ["native_tags", "hierarchy", "attachments", "advanced_metadata"]]
}
func selectedNativeMetadata(_ reminder:EKReminder,_ selections:[String:Any])->[String:Any]? {
    var combined:[String:Any]=[:];var coverage:[String]=[]
        for key in ["include_native_metadata","include_native_tags","include_native_hierarchy","include_native_children","include_native_urgent","include_native_section","include_native_early","include_native_assignment","include_native_attachments"] {
            guard let requested=selections[key] else {continue}
            guard let number=requested as? NSNumber,CFGetTypeID(number)==CFBooleanGetTypeID() else {fail("invalid_request")}
            if !number.boolValue {continue}
            let identity=["id":reminder.calendarItemIdentifier,"source_id":reminder.calendar.source.sourceIdentifier,"list_id":reminder.calendar.calendarIdentifier]
            guard let encoded=try? JSONSerialization.data(withJSONObject:identity) else {fail("read_failed")}
            let data=key=="include_native_attachments" ? ICloudRemindersReadAttachments(encoded) : key=="include_native_assignment" ? ICloudRemindersReadAssignment(encoded) : key=="include_native_early" ? ICloudRemindersReadEarly(encoded) : key=="include_native_section" ? ICloudRemindersReadSection(encoded) : key=="include_native_urgent" ? ICloudRemindersReadUrgent(encoded) : key=="include_native_tags" ? ICloudRemindersReadTags(encoded) : key=="include_native_hierarchy" ? ICloudRemindersReadHierarchy(encoded) : key=="include_native_children" ? ICloudRemindersReadChildren(encoded) : ICloudRemindersReadFlag(encoded)
            guard let metadata=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any] else {fail("read_failed")}
            if metadata["error"] != nil {respond(metadata)}
            guard let fields=metadata["native_metadata"] as? [String:Any],fields["id"] as? String==identity["id"],fields["source_id"] as? String==identity["source_id"],fields["list_id"] as? String==identity["list_id"],let observed=fields["coverage"] as? [String] else {fail("read_failed")}
            combined.merge(fields){_,new in new};coverage.append(contentsOf:observed)
        }
        if !coverage.isEmpty {
            combined["coverage"]=coverage
            if coverage.count>1 {combined["backend"] = coverage == ["flagged","tags"] ? "reminderkit-flags-and-tags-v1" : "reminderkit-combined-v1"}
            return combined
        }
    return nil
}
func nativeSnapshotMatches(_ reminder:EKReminder,_ expected:[String:Any])->Bool {
    guard let observed=selectedNativeMetadata(reminder,["include_native_metadata":true,"include_native_tags":true,"include_native_hierarchy":true,"include_native_children":true,"include_native_section":true,"include_native_early":true]) else {return false}
    return NSDictionary(dictionary:observed).isEqual(to:expected)
}
if action == "get" {
    guard let id = req["id"] as? String else { fail("invalid_request") }
    guard !lists.isEmpty else { fail("not_found_in_scope") }
    let predicate = store.predicateForReminders(in: lists)
    store.fetchReminders(matching: predicate) { reminders in
        guard let reminders = reminders else { fail("read_failed") }
        guard reminders.count <= 100000 else { fail("query_limit") }
        guard let reminder = reminders.first(where: { $0.calendarItemIdentifier == id }) else { fail("not_found_in_scope") }
        var result=object(reminder)
        if let native=selectedNativeMetadata(reminder,req){result["native_metadata"]=native}
        respond(["protocol_version": 1, "item": result])
    }
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 30))
    fail("read_timeout")
}

guard let limit = req["limit"] as? Int, (1...100).contains(limit),
      let offset = req["offset"] as? Int, (0...100000).contains(offset) else { fail("invalid_page") }
let requestedList = req["list_id"] as? String
let selected = lists.filter { requestedList == nil || $0.calendarIdentifier == requestedList }
let nativeFlag=req["flagged"]
let nativeUrgent=req["urgent"]
let nativeTag=req["tag"]
let nativeAttachment=req["has_native_attachments"]
if let attachment=nativeAttachment {guard let value=attachment as? NSNumber,CFGetTypeID(value)==CFBooleanGetTypeID() else {fail("invalid_request")}}
let nativeAssignment=req["has_current_assignment"]
if let assignment=nativeAssignment {guard let value=assignment as? NSNumber,CFGetTypeID(value)==CFBooleanGetTypeID() else {fail("invalid_request")}}
if let urgent=nativeUrgent {guard let value=urgent as? NSNumber,CFGetTypeID(value)==CFBooleanGetTypeID() else {fail("invalid_request")}}
if let flag=nativeFlag {guard let value=flag as? NSNumber,CFGetTypeID(value)==CFBooleanGetTypeID() else {fail("invalid_request")}}
if let raw=nativeTag {
    guard let tag=raw as? String,!tag.isEmpty,tag.lengthOfBytes(using:.utf8)<=512,!tag.unicodeScalars.contains(where:{$0.value<32}) else {fail("invalid_request")}
}
let completionFromText=req["completed_from"] as? String
let completionBeforeText=req["completed_before"] as? String
if (req["completed_from"] != nil && completionFromText == nil) || (req["completed_before"] != nil && completionBeforeText == nil) {fail("invalid_date_filter")}
let completionFormatter=ISO8601DateFormatter()
let completionFrom=completionFromText.flatMap {completionFormatter.date(from:$0)}
let completionBefore=completionBeforeText.flatMap {completionFormatter.date(from:$0)}
if (completionFromText != nil && completionFrom == nil) || (completionBeforeText != nil && completionBefore == nil) || (completionFrom != nil && completionBefore != nil && completionFrom! >= completionBefore!) || ((completionFrom != nil || completionBefore != nil) && (req["completed"] as? Bool)==false) {fail("invalid_date_filter")}
let completionEvidence:[String:Any]? = (completionFromText != nil || completionBeforeText != nil) ? ["from":completionFromText ?? NSNull() as Any,"before":completionBeforeText ?? NSNull() as Any,"semantics":"utc_inclusive_from_exclusive_before"] : nil
let modifiedFromText=req["modified_from"] as? String
let modifiedBeforeText=req["modified_before"] as? String
if (req["modified_from"] != nil && modifiedFromText == nil) || (req["modified_before"] != nil && modifiedBeforeText == nil) {fail("invalid_date_filter")}
let modifiedFrom=modifiedFromText.flatMap {completionFormatter.date(from:$0)}
let modifiedBefore=modifiedBeforeText.flatMap {completionFormatter.date(from:$0)}
if (modifiedFromText != nil && modifiedFrom == nil) || (modifiedBeforeText != nil && modifiedBefore == nil) || (modifiedFrom != nil && modifiedBefore != nil && modifiedFrom! >= modifiedBefore!) {fail("invalid_date_filter")}
let modificationEvidence:[String:Any]? = (modifiedFromText != nil || modifiedBeforeText != nil) ? ["from":modifiedFromText ?? NSNull() as Any,"before":modifiedBeforeText ?? NSNull() as Any,"semantics":"utc_inclusive_from_exclusive_before","unknown_modified_excluded":true] : nil
let createdFromText=req["created_from"] as? String
let createdBeforeText=req["created_before"] as? String
if (req["created_from"] != nil && createdFromText == nil) || (req["created_before"] != nil && createdBeforeText == nil) {fail("invalid_date_filter")}
let createdFrom=createdFromText.flatMap {completionFormatter.date(from:$0)}
let createdBefore=createdBeforeText.flatMap {completionFormatter.date(from:$0)}
if (createdFromText != nil && createdFrom == nil) || (createdBeforeText != nil && createdBefore == nil) || (createdFrom != nil && createdBefore != nil && createdFrom! >= createdBefore!) {fail("invalid_date_filter")}
let creationEvidence:[String:Any]? = (createdFromText != nil || createdBeforeText != nil) ? ["from":createdFromText ?? NSNull() as Any,"before":createdBeforeText ?? NSNull() as Any,"semantics":"utc_inclusive_from_exclusive_before","unknown_created_excluded":true] : nil
func completionInRange(_ completed:Bool,_ value:Date?,_ from:Date?,_ before:Date?)->Bool {
    if from == nil && before == nil {return true}
    guard completed,let value=value else {return false}
    return (from == nil || value >= from!) && (before == nil || value < before!)
}
func modificationInRange(_ value:Date?,_ from:Date?,_ before:Date?)->Bool {
    if from == nil && before == nil {return true}
    guard let value=value else {return false}
    return (from == nil || value >= from!) && (before == nil || value < before!)
}
guard !selected.isEmpty else {
    if requestedList != nil { fail("not_found_in_scope") }
    if nativeFlag != nil || nativeTag != nil || nativeUrgent != nil || nativeAssignment != nil || nativeAttachment != nil {
        let evidence:[String:Any]=["scanned":0,"matched":0,"observation_consistency":"separate_provider_reads"]
        var response:[String:Any]=["protocol_version":1,"items":[],"done":true,"next_offset":offset,"pagination_consistency":"live_offset_may_shift"]
        if nativeFlag != nil {response["native_flag_filter"]=evidence}
        if nativeUrgent != nil {response["native_urgent_filter"]=evidence}
        if nativeTag != nil {response["native_tag_filter"]=evidence}
        if nativeAssignment != nil {response["native_assignment_filter"]=evidence}
        if nativeAttachment != nil {response["native_attachment_filter"]=evidence}
        if let completionEvidence=completionEvidence {response["completion_filter"]=completionEvidence}
        if let modificationEvidence=modificationEvidence {response["modification_filter"]=modificationEvidence}
        if let creationEvidence=creationEvidence {response["creation_filter"]=creationEvidence}
        respond(response)
    }
    var response:[String:Any]=["protocol_version": 1, "items": [], "done": true, "next_offset": offset,
             "pagination_consistency": "live_offset_may_shift"]
    if let completionEvidence=completionEvidence {response["completion_filter"]=completionEvidence}
    if let modificationEvidence=modificationEvidence {response["modification_filter"]=modificationEvidence}
    if let creationEvidence=creationEvidence {response["creation_filter"]=creationEvidence}
    respond(response)
}
let dueFilter = req["due_date"] as? String
let dueFrom = req["due_from"] as? String
let dueThrough = req["due_through"] as? String
let priorities = req["priorities"] as? [Int]
if let priorities = priorities {
    guard !priorities.isEmpty, priorities.count<=4, Set(priorities).count==priorities.count,
          priorities.allSatisfy({ [0,1,5,9].contains($0) }) else { fail("invalid_priority_filter") }
}
if dueFilter != nil && (dueFrom != nil || dueThrough != nil) { fail("invalid_date_filter") }
if let low=dueFrom, let high=dueThrough, low>high { fail("invalid_date_filter") }
let zone = TimeZone(identifier: req["timezone"] as? String ?? "")
if (dueFilter != nil || dueFrom != nil || dueThrough != nil) && zone == nil { fail("invalid_date_filter") }
func dueDay(_ reminder: EKReminder) -> String? {
    guard let d = reminder.dueDateComponents else { return nil }
    if d.hour == nil && d.minute == nil {
        guard let y = d.year, let m = d.month, let day = d.day else { return nil }
        return String(format: "%04d-%02d-%02d", y, m, day)
    }
    var calendar = d.calendar ?? Calendar(identifier: .gregorian)
    calendar.timeZone = d.timeZone ?? zone ?? TimeZone.current
    guard let instant = calendar.date(from: d) else { return nil }
    var display = Calendar(identifier: .gregorian)
    display.timeZone = zone ?? TimeZone.current
    let converted = display.dateComponents([.year, .month, .day], from: instant)
    guard let y = converted.year, let m = converted.month, let day = converted.day else { return nil }
    return String(format: "%04d-%02d-%02d", y, m, day)
}
let predicate = store.predicateForReminders(in: selected)
store.fetchReminders(matching: predicate) { reminders in
    guard let reminders = reminders else { fail("read_failed") }
    guard reminders.count <= 100000 else { fail("query_limit") }
    let completed = req["completed"] as? Bool
    let text = req["text"] as? String
    let filtered = reminders.filter { r in
        let day = dueDay(r)
        let dueMatches = dueFilter == nil || (day != nil &&
            (req["include_overdue"] as? Bool == true ? day! <= dueFilter! : day! == dueFilter!))
        let rangeMatches=(dueFrom == nil && dueThrough == nil) || (day != nil &&
            (dueFrom == nil || day! >= dueFrom!) && (dueThrough == nil || day! <= dueThrough!))
        return dueMatches && rangeMatches && completionInRange(r.isCompleted,r.completionDate,completionFrom,completionBefore) && modificationInRange(r.lastModifiedDate,modifiedFrom,modifiedBefore) && modificationInRange(r.creationDate,createdFrom,createdBefore) && (priorities == nil || priorities!.contains(r.priority)) && (completed == nil || r.isCompleted == completed) &&
        (text == nil || ((r.title ?? "") + "\n" + (r.notes ?? "")).localizedCaseInsensitiveContains(text!))
    }.sorted { $0.calendarItemIdentifier < $1.calendarItemIdentifier }
    if nativeFlag != nil || nativeTag != nil || nativeUrgent != nil || nativeAssignment != nil || nativeAttachment != nil {
        let scanned=Array(filtered.dropFirst(offset).prefix(limit))
        var matches:[[String:Any]]=[]
        for reminder in scanned {
            let selections:[String:Any]=["include_native_metadata":nativeFlag != nil,"include_native_tags":nativeTag != nil,"include_native_urgent":nativeUrgent != nil,"include_native_assignment":nativeAssignment != nil,"include_native_attachments":nativeAttachment != nil]
            guard let native=selectedNativeMetadata(reminder,selections) else {fail("private_metadata_unavailable")}
            if let expected=nativeFlag as? NSNumber {
                guard let value=native["flagged"] as? NSNumber,CFGetTypeID(value)==CFBooleanGetTypeID() else {fail("private_metadata_unavailable")}
                if value.boolValue != expected.boolValue {continue}
            }
            if let expected=nativeUrgent as? NSNumber {
                guard let value=native["urgent"] as? NSNumber,CFGetTypeID(value)==CFBooleanGetTypeID() else {fail("private_metadata_unavailable")}
                if value.boolValue != expected.boolValue {continue}
            }
            if let expected=nativeAssignment as? NSNumber {
                guard let value=native["current_assignment_reported"] as? NSNumber,CFGetTypeID(value)==CFBooleanGetTypeID() else {fail("private_metadata_unavailable")}
                if value.boolValue != expected.boolValue {continue}
            }
            if let expected=nativeAttachment as? NSNumber {
                guard let values=native["attachments"] as? [[String:Any]],native["attachment_metadata_complete"] as? Bool == false else {fail("private_metadata_unavailable")}
                if (!values.isEmpty) != expected.boolValue {continue}
            }
            if let expected=nativeTag as? String {
                guard native["tags_complete"] as? Bool == true,let tags=native["tags"] as? [[String:Any]] else {fail("private_metadata_unavailable")}
                if !tags.contains(where:{$0["name"] as? String == expected}) {continue}
            }
            var item=object(reminder);item["native_metadata"]=native;matches.append(item)
        }
        let next=offset+scanned.count
        let evidence:[String:Any]=["scanned":scanned.count,"matched":matches.count,"observation_consistency":"separate_provider_reads"]
        var response:[String:Any]=["protocol_version":1,"items":matches,"done":next>=filtered.count,"next_offset":next,"pagination_consistency":"live_offset_may_shift"]
        if nativeFlag != nil {response["native_flag_filter"]=evidence}
        if nativeUrgent != nil {response["native_urgent_filter"]=evidence}
        if nativeTag != nil {response["native_tag_filter"]=evidence}
        if nativeAssignment != nil {response["native_assignment_filter"]=evidence}
        if nativeAttachment != nil {response["native_attachment_filter"]=evidence}
        if let completionEvidence=completionEvidence {response["completion_filter"]=completionEvidence}
        if let modificationEvidence=modificationEvidence {response["modification_filter"]=modificationEvidence}
        if let creationEvidence=creationEvidence {response["creation_filter"]=creationEvidence}
        respond(response)
    }
    let page = Array(filtered.dropFirst(offset).prefix(limit)).map(object)
    let next = offset + page.count
    var response:[String:Any]=["protocol_version": 1, "items": page, "done": next >= filtered.count,
             "next_offset": next, "pagination_consistency": "live_offset_may_shift"]
    if let completionEvidence=completionEvidence {response["completion_filter"]=completionEvidence}
    if let modificationEvidence=modificationEvidence {response["modification_filter"]=modificationEvidence}
    if let creationEvidence=creationEvidence {response["creation_filter"]=creationEvidence}
    respond(response)
}
RunLoop.current.run(until: Date(timeIntervalSinceNow: 30))
fail("read_timeout")
