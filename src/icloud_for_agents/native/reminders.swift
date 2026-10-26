// Fixed native protocol: EventKit reads and Notes creation transport.
// The Python approved-plan executor enforces mutation authorization; no implicit permission requests.
import Foundation
import EventKit
import OSAKit
import ApplicationServices

func respond(_ value: [String: Any]) -> Never {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]), data.count <= 2_000_000 else {
        FileHandle.standardOutput.write(Data("{\"error\":\"response_limit\",\"protocol_version\":1}".utf8)); exit(0)
    }
    FileHandle.standardOutput.write(data); exit(0)
}
func fail(_ code: String) -> Never { respond(["protocol_version": 1, "error": code]) }
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
let data = FileHandle.standardInput.readData(ofLength: 65537)
guard data.count <= 65536, let req = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
      req["protocol_version"] as? Int == 1, let action = req["action"] as? String else { fail("invalid_request") }
if req["service"] as? String == "notes" {
    guard ["status", "request_access", "sources", "folders", "folder_get", "query", "get", "apply"].contains(action) else { fail("unsupported_action") }
    if action == "status" || action == "request_access" {
        let target = NSAppleEventDescriptor(bundleIdentifier: "com.apple.Notes")
        let result = AEDeterminePermissionToAutomateTarget(target.aeDesc, typeWildCard, typeWildCard, action == "request_access")
        respond(["protocol_version": 1, "service": "notes", "permission_status": result,
                 "permission_granted": result == noErr, "application_writes_enabled": false])
    }
    guard let url = Bundle.main.url(forResource: action == "apply" ? "notes-write" : "notes", withExtension: "js"),
          let source = try? String(contentsOf: url, encoding: .utf8),
          let language = OSALanguage(forName: "JavaScript") else { fail("notes_bridge_unavailable") }
    // Only base64 request data is inserted; caller text is never executable source.
    let encoded = data.base64EncodedString()
    let entry = action == "apply" ? "applyNotes" : "readNotes"
    let bridge = "\nfunction run() { return " + entry + "(Application('Notes'), JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding($.NSData.alloc.initWithBase64EncodedStringOptions('" + encoded + "',0), $.NSUTF8StringEncoding)))); }"
    let script = OSAScript(source: source + bridge, language: language)
    var diagnostic: NSDictionary?
    guard let output = script.executeAndReturnError(&diagnostic)?.stringValue,
          let value = (try? JSONSerialization.jsonObject(with: Data(output.utf8))) as? [String: Any] else {
        let code = diagnostic?[OSAScriptErrorNumberKey] as? Int ?? -1
        if code == -1743 || code == -1744 { fail("permission_required") }
        fail("notes_read_failed")
    }
    respond(value)
}
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
let actions = ["status", "sources", "lists", "query", "get", "apply"]
guard actions.contains(action) else { fail("unsupported_action") }
if action == "status" {
    respond(["protocol_version": 1, "service": "reminders", "access": permission,
             "read_only": true, "requests_permissions": false])
}
guard status == .fullAccess else { fail("permission_required") }
let store = EKEventStore()
let allLists = store.calendars(for: .reminder)
if action == "sources" {
    var seen = Set<String>()
    let sources = allLists.compactMap { list -> [String: Any]? in
        let source = list.source!
        guard seen.insert(source.sourceIdentifier).inserted else { return nil }
        return ["id": source.sourceIdentifier, "title": source.title, "type": source.sourceType.rawValue]
    }
    respond(["protocol_version": 1, "sources": sources])
}
guard let sourceIDs = req["source_ids"] as? [String], !sourceIDs.isEmpty, sourceIDs.count <= 100 else { fail("source_scope_required") }
let lists = allLists.filter { sourceIDs.contains($0.source.sourceIdentifier) }
if action == "apply" {
    guard let item = req["change"] as? [String: Any], let operation = item["operation"] as? String,
          let sourceID = item["source_id"] as? String, sourceIDs.contains(sourceID),
          let fields = item["fields"] as? [String: Any], let title = fields["title"] as? String,
          !title.isEmpty, title.count <= 1000 else { fail("invalid_mutation") }
    if operation == "create_list" {
        guard let source = store.sources.first(where: { $0.sourceIdentifier == sourceID }) else { fail("not_found_in_scope") }
        guard !lists.contains(where: { $0.source.sourceIdentifier == sourceID && $0.title == title }) else { fail("duplicate_list") }
        let list = EKCalendar(for: .reminder, eventStore: store)
        list.source = source; list.title = title
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
    let reminder = EKReminder(eventStore: store)
    reminder.calendar = list; reminder.title = title
    reminder.notes = fields["notes"] as? String ?? ""
    reminder.priority = fields["priority"] as? Int ?? 0
    reminder.dueDateComponents = components(fields["due"])
    reminder.startDateComponents = components(fields["start"])
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
    respond(["protocol_version": 1, "lists": page.map { list -> [String: Any] in
        ["id": list.calendarIdentifier, "title": String(list.title.prefix(1000)), "source_id": list.source.sourceIdentifier,
         "writable": list.allowsContentModifications]
    }, "done": offset + page.count >= sorted.count, "next_offset": offset + page.count,
       "pagination_consistency": "live_offset_may_shift"])
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
     "has_recurrence": reminder.hasRecurrenceRules, "has_alarms": reminder.hasAlarms,
     "content_complete": false, "omitted_features": ["native_tags", "hierarchy", "attachments", "advanced_metadata"]]
}
if action == "get" {
    guard let id = req["id"] as? String else { fail("invalid_request") }
    guard !lists.isEmpty else { fail("not_found_in_scope") }
    let predicate = store.predicateForReminders(in: lists)
    store.fetchReminders(matching: predicate) { reminders in
        guard let reminders = reminders else { fail("read_failed") }
        guard reminders.count <= 100000 else { fail("query_limit") }
        guard let reminder = reminders.first(where: { $0.calendarItemIdentifier == id }) else { fail("not_found_in_scope") }
        respond(["protocol_version": 1, "item": object(reminder)])
    }
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 20))
    fail("read_timeout")
}

guard let limit = req["limit"] as? Int, (1...100).contains(limit),
      let offset = req["offset"] as? Int, (0...100000).contains(offset) else { fail("invalid_page") }
let requestedList = req["list_id"] as? String
let selected = lists.filter { requestedList == nil || $0.calendarIdentifier == requestedList }
guard !selected.isEmpty else {
    if requestedList != nil { fail("not_found_in_scope") }
    respond(["protocol_version": 1, "items": [], "done": true])
}
let dueFilter = req["due_date"] as? String
let zone = TimeZone(identifier: req["timezone"] as? String ?? "")
if dueFilter != nil && zone == nil { fail("invalid_date_filter") }
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
        return dueMatches && (completed == nil || r.isCompleted == completed) &&
        (text == nil || ((r.title ?? "") + "\n" + (r.notes ?? "")).localizedCaseInsensitiveContains(text!))
    }.sorted { $0.calendarItemIdentifier < $1.calendarItemIdentifier }
    let page = Array(filtered.dropFirst(offset).prefix(limit)).map(object)
    let next = offset + page.count
    respond(["protocol_version": 1, "items": page, "done": next >= filtered.count,
             "next_offset": next, "pagination_consistency": "live_offset_may_shift"])
}
RunLoop.current.run(until: Date(timeIntervalSinceNow: 20))
fail("read_timeout")
