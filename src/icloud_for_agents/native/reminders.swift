// Fixed read-only EventKit protocol. No save/remove or implicit permission requests.
import Foundation
import EventKit

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
let actions = ["status", "sources", "lists", "query", "get"]
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
if action == "lists" {
    respond(["protocol_version": 1, "lists": lists.map { list -> [String: Any] in
        ["id": list.calendarIdentifier, "title": list.title, "source_id": list.source.sourceIdentifier,
         "writable": list.allowsContentModifications]
    }])
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
    guard let id = req["id"] as? String,
          let reminder = store.calendarItem(withIdentifier: id) as? EKReminder,
          sourceIDs.contains(reminder.calendar.source.sourceIdentifier) else { fail("not_found_in_scope") }
    respond(["protocol_version": 1, "item": object(reminder)])
}
guard let limit = req["limit"] as? Int, (1...100).contains(limit),
      let offset = req["offset"] as? Int, (0...100000).contains(offset) else { fail("invalid_page") }
let requestedList = req["list_id"] as? String
let selected = lists.filter { requestedList == nil || $0.calendarIdentifier == requestedList }
guard !selected.isEmpty else {
    if requestedList != nil { fail("not_found_in_scope") }
    respond(["protocol_version": 1, "items": [], "done": true])
}
let predicate = store.predicateForReminders(in: selected)
store.fetchReminders(matching: predicate) { reminders in
    guard let reminders = reminders else { fail("read_failed") }
    guard reminders.count <= 100000 else { fail("query_limit") }
    let completed = req["completed"] as? Bool
    let text = req["text"] as? String
    let filtered = reminders.filter { r in
        (completed == nil || r.isCompleted == completed) &&
        (text == nil || ((r.title ?? "") + "\n" + (r.notes ?? "")).localizedCaseInsensitiveContains(text!))
    }.sorted { $0.calendarItemIdentifier < $1.calendarItemIdentifier }
    let page = Array(filtered.dropFirst(offset).prefix(limit)).map(object)
    let next = offset + page.count
    respond(["protocol_version": 1, "items": page, "done": next >= filtered.count,
             "next_offset": next, "pagination_consistency": "live_offset_may_shift"])
}
RunLoop.current.run(until: Date(timeIntervalSinceNow: 20))
fail("read_timeout")
