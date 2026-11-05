# Tools and everyday workflows


Each selected service registers its own tools. Calendar and Contacts share a DAV execution tool, which refuses plans targeting a disabled service. Local owner-review tools are available when any service is selected. See [native-service setup](../getting-started/native-services.md) for Notes and Reminders operations and permissions. Explicit Mail/Calendar/Contacts switches override the legacy combined DAV fallback. In read-only mode, ordinary Mail and DAV writes remain refused; exact Calendar one-time proposals and local approval reviews remain available under their configured policies. Native services have separate access and approval controls. An advertised write tool does not itself grant permission.

When a proposal requires a local owner receipt, `owner_request_review` opens a fixed review window on the Mac. The owner reviews the complete proposal and chooses Approve or Cancel; `owner_review_status` reports the outcome. Approval covers only that exact proposal and does not execute it. For an optional managed Mail policy proposal, use `scope: "managed-policy"` with both tools. The connected client cannot supply the review contents or approve the window itself.

## Mail tools

| Tool | Purpose and important input | Read-only runtime |
| --- | --- | --- |
| `email_list_folders` | Return exact folder names, delimiters, and server flags | Allowed |
| `email_page` | Read a bounded oldest-first header page; use exact folder and retain its cursor/filter identity | Allowed |
| `email_inspect` | Read one bounded text message (plain preferred, safe HTML fallback) with exact folder, UIDVALIDITY, and message UID | Allowed |
| `email_plan_status` | Read an exact local plan's status without retrying its mail action | Allowed |
| `email_set_read_state` | Add/remove only Seen for 1–100 explicit UIDs | Refused |
| `email_archive` | Move 1–100 explicit messages to the resolved existing Archive | Refused |
| `email_restore_from_trash` | Move explicit Trash UIDs to an exact existing destination | Refused |
| `email_prepare_trash` | Create a local proposal for an exact 1–100-message Trash batch and reason; moves nothing | Refused |
| `email_prepare_send` | Create a local exact-recipient, plain-text send proposal; sends nothing | Refused |
| `email_execute_approved` | Execute the exact plan once under the selected approval/standing policy | Refused |
| `email_prepare_cleanup_grant` | Review an exact standing Mail cleanup scope without activating it | Refused |
| `email_activate_cleanup_grant` | Activate the exact approved standing scope without changing messages | Refused |
| `email_cleanup_grant_status` | Inspect the installed standing scope | Allowed |
| `email_revoke_cleanup_grant` | Revoke the exact installed scope for future batches | Allowed |

In a separately approved write-enabled runtime, read-state/archive/restore do not require the special local receipt. They still need human task authorization and any client confirmation. Send and Trash require appropriate task authorization and the selected strict receipt, trusted-client or standing-cleanup path; see [approval policies](../user-guide/approvals.md). Standing cleanup refuses stars and out-of-scope folders before ordinary receipt handling. Do not enable writes as an incidental setup repair.

## Review a mailbox without skipping older mail

Start by listing folders rather than guessing English names such as Archive or Trash. Use one exact selectable name with `email_page`; the default page size is 50 and the permitted range is 1–100. Optional sender/subject/unread filters bind the cursor to that query.

Save the returned `next_cursor` only after reviewing or recording the page's outcome. Continue even when a page contains no messages, until `done=true`. A call searches at most 2,000 numeric UID positions and fetches at most 100 bounded headers, so an empty UID window does not mean the mailbox is finished.

Each scan holds a fixed high-UID ceiling. New arrivals are not silently inserted ahead of older backlog. Save `completed_snapshot` on completion. A later incremental scan uses that snapshot's `high_uid` as `after_uid` and its `uidvalidity` as `expected_uidvalidity`. If validity, account, folder, or filters change, rediscover/restart instead of reusing identities. UIDs are not stable across different folders or UIDVALIDITY values.

Other mail clients can still move messages or change flags while a scan is running. An unread-only scan is not a full-folder audit; an unfiltered backlog pass is more robust against changing UNSEEN membership. A vanished or failed-to-parse item remains an explicit exception to reconcile, not proof it was reviewed.

Inspection fetches at most 256 KiB with BODY.PEEK and returns at most 50,000 text characters. Plain MIME text is preferred; HTML-only mail gets bounded static HTML-to-text extraction. No browser rendering, link/image fetch, script execution or attachment inspection occurs. Recognized hidden elements/styles and transparent tracking footers are excluded. `body_format` identifies plain, html_to_text or none; `html_only_or_no_plaintext` remains true for an HTML fallback and does not imply an empty body. `truncated` includes wire, decoded output and structural bounds; `mime_defects_detected` flags malformed input. Complex CSS/image-only content may still yield an incomplete or blank view. All extracted content remains untrusted. Do not discard mail because its bounded preview has no readable body.

## Review and execute a send or Trash batch

This strict-mode sequence applies only after write access and that action are approved. Preparing a proposal does not authorize or execute it. Explicitly enabled trusted-client mode relies on client authorization instead of a local receipt; it is not proof of independently authenticated human approval.

1. Prepare exact recipients/content or exact Trash message identities and reason. Review the returned proposal and its plan ID.
2. The assistant calls `owner_request_review` for that plan. Review the complete proposal in the Mac window and choose Approve or Cancel. The assistant can check `owner_review_status`; a chat message alone does not create a strict receipt.
3. Call `email_execute_approved` with that same plan ID. The receipt binds the proposal, account, and 30-minute proposal expiry. It cannot authorize another batch, edited recipient, or future action.
4. After interruption or an uncertain result, inspect `email_plan_status` before doing anything else. Never resend/re-trash by replaying the plan or creating an automatic replacement.

The assistant must not click the owner's approval button, synthesize a decision, or write receipt files. Read-only mode refuses execution even if a receipt exists; local approval does not override the launch mode.

Successful moves return destination UIDVALIDITY and source-to-destination UID mappings. Use destination identities when inspecting or restoring. A failed MOVE verification may occur after the server moved mail, and a lost SMTP response may follow delivery. Local replay protection does not prove an external action never happened. Reconcile source/destination or Sent and, when appropriate, the recipient before authorizing a replacement.

Trash is recoverable only while the mail provider retains it. There is no empty-Trash or permanent-purge tool. Sending supports bare explicit email addresses, at most 50 combined recipients, a single-line subject up to 998 characters, and a plain-text UTF-8 body up to 100,000 bytes. Bcc recipients are not placed in the sent message headers.

## Calendar and contact tools

Select Calendar with `ICLOUD_ENABLE_CALENDAR=1` and Contacts with `ICLOUD_ENABLE_CONTACTS=1`. The combined `ICLOUD_ENABLE_DAV_READ=1` remains a fallback only when explicit switches are absent. It does not override runtime read-only access. Credential-free stdio tests exercise this boundary with synthetic fixtures.

| Tool | Behavior |
| --- | --- |
| `calendar_list` | Discover exact calendar collections |
| `calendar_events` | Bounded calendar resources, timezone-aware range <=31 days; optionally request expanded occurrences |
| `contacts_addressbooks` | Discover exact addressbooks |
| `contacts_list` | Metadata pages of 1–100 references; bounded live listing, not an immutable snapshot |
| `contacts_get` | One bounded raw vCard, <=256 KiB |
| `calendar_prepare_changes` | Prepare 1–20 explicit personal-event create/update/delete operations; no writes yet |
| `calendar_prepare_once` | Prepare one exact Calendar batch from read-only access; does not change saved permissions |
| `contacts_prepare_changes` | Prepare 1–20 explicit contact create/update/delete operations; no writes yet |
| `dav_execute_approved` | Execute an exact calendar/contact plan under the selected approval policy |

Ordinary change preparation is refused in read-only mode. `calendar_prepare_once` instead reviews one exact Calendar batch without changing saved access. For one explicitly owner-approved irreversible Calendar deletion, an already enabled trusted-client policy can use `deletion_authorization: "trusted-client"` and rely on the client to obtain owner approval; this is not an independent human receipt. The default, strict policy, Contacts and ordinary Calendar deletion require a local receipt. Disabled Calendar access cannot use this path. Contact changes and ordinary Calendar changes need separately authorized `write-enabled` access.

For a bounded view of repeating events, `calendar_events` accepts `expand_recurring: true`. It asks the provider to expand occurrences within the requested window, with at most 200 returned occurrences. The result contains the original resource URL and a server-generated read-only projection; do not use that projection as an editable series snapshot. A bounded read-only iCloud test returned occurrences for existing recurring resources; complete recurrence coverage remains unverified. The default remains the existing raw resource view.

Each update/deletion binds a strong ETag and private before-content snapshot; conditional If-Match protects against drift. Create uses an assigned resource URL and If-None-Match: *. One exact approval covers the bounded batch, not a new question per item. Interrupted or partially completed batches are never retried/rolled back automatically. Check exact resources and completed outcomes before a replacement plan.

DAV has no protocol Trash here. Deletion requires explicit owner approval and `irreversible_confirmed: true`. The local-receipt route uses the owner's Mac review window; the one-time trusted-client route relies on client confirmation instead. The snapshot is retained privately for review/manual recovery, not a promise of server undo. Prefer update over deletion when possible. Provider-native recovery availability is not verified.

Personal single-event changes support summary/start/end/description/location/timezone/url/free/private. Named IANA timezone is validated against explicit offsets; UTC timestamps plus X-ICLOUD-MCP-TIMEZONE metadata are stored, not a native VTIMEZONE component.

For a new recurring event, prepare a separate one-item `create` proposal with `fields.recurrence`: an explicit `frequency` (`daily`, `weekly`, `monthly` or `yearly`), optional integer `interval`, either `count` or an inclusive UTC `until`, and optional weekly `weekdays` (`MO` through `SU`, including the first day). Start and end need explicit offsets matching an explicit IANA timezone. For a named local timezone, the selected calendar must return a matching supported timezone definition; the plan binds its hash and includes a bounded comparison with local timezone rules, then rechecks it before writing. This sampled comparison does not prove every occurrence or future timezone law. Local-time events must start and end on the same day and their time span must not overlap a daylight-saving transition's clock-change interval. Ambiguous or nonexistent initial local times refuse. Monthly and yearly rules may skip invalid calendar dates, as can recurring instances in a daylight-saving gap. For an existing supported single-component recurring series without attendees, exceptions, alarms or unsupported timezone components, use `recurrence_scope: "series"` with an `update` of summary, description, location, URL, free or private state. To replace its schedule, provide complete `start`, `end`, `timezone` and `recurrence` fields together in a separate one-item series update. This replaces the schedule for the entire series, including past occurrences; a count starts from the new first occurrence. Future-only changes are refused.

For one occurrence in a supported simple UTC series, use a separate one-item proposal with `recurrence_scope: "occurrence"`, the original occurrence start as a whole-second UTC `occurrence_start`, and `operation: "update"` with the supported text, URL, free or private fields. To remove that occurrence instead, use `operation: "delete_occurrence"` and `irreversible_confirmed: true`; obtain owner approval through the configured local receipt or the explicitly selected one-time trusted-client route. The series is retained. The provider must confirm the exact original instance and unchanged resource before the proposal and again before writing. This confirmation depends on provider expansion and does not independently prove recurrence completeness. Series with existing exceptions, participants, alarms, non-UTC times or unsupported properties are refused, as are time changes. Isolated iCloud tests verified both an occurrence update and removal of one occurrence while preserving its neighbors.

To delete a supported one-master series without attendees, exceptions or unsupported timezone components, use `operation: "delete"`, `recurrence_scope: "series"` and `irreversible_confirmed: true`. Approval covers the entire irreversible series deletion, including its alarms. Creation and updates verify the reviewed event after writing; series deletion checks that the resource is absent. A failed or missing readback is uncertain and cannot be replayed automatically. Isolated iCloud tests verified creation, whole-series updates, rescheduling across daylight saving, and whole-series deletion under the trusted-client policy. An existing shared calendar can synchronize changes to other people; no attendee scheduling is performed.

Events with ATTENDEE, ORGANIZER, or METHOD are refused: invitations, scheduling responses, cancellations, and external scheduling emails require a separate explicit workflow and are not silently sent. Contact fields support full_name, emails, phones, organization, and note; unknown existing contact properties/UID and nested calendar alarms are retained. No iCloud Drive or contract-file access is implemented.


## Notes and Reminders tools

Choose either service independently. Start with `notes_access_status` or `reminders_access_status` to see its configured scope and available operations, then `notes_status` or `reminders_status` to check app access without requesting consent. Discover exact accounts with `notes_sources` or `reminders_sources`. After selecting the approved source IDs, use `notes_folders` and `notes_query`, or `reminders_lists` and `reminders_query`, to discover exact content identities. `notes_get` and `reminders_get` read individual items. Follow each returned `next_offset` until `done`, including empty filtered pages.

The shared workflow below exists under **both** prefixes. For example, `prepare_grant` means `notes_prepare_grant` for Notes and `reminders_prepare_grant` for Reminders; use the same prefix throughout a plan or grant.

| Tool suffix | Purpose |
| --- | --- |
| `prepare_os_access`, `request_os_access` | Review and consume one request for a user-handled macOS permission dialog; no content change or source selection |
| `prepare_changes` | Review exact changes when ordinary write access is enabled |
| `prepare_once` | Review one exact batch from read-only access without changing saved access settings |
| `execute_approved` | Execute the exact plan once under the configured approval policy |
| `plan_status`, `cancel_plan` | Inspect an exact plan or cancel an unstarted change; cancellation cannot recall an in-flight operation |
| `prepare_grant`, `activate_grant` | Review and activate exact ongoing/scenario authority |
| `prepare_grant_replacement` | Review a replacement scope and carry existing quota history into its approved activation |
| `grant_status`, `revoke_grant` | Inspect or revoke existing authority |
| `prepare_with_grant` | Review exact changes within one active grant |
| `finalize_grant`, `finalize_stopped_plan` | Complete the supported interrupted local audit transition without replaying a native write |
| `reconcile_created`, `reconcile_known_changes` | Independently verify recorded returned identities and intended effects where the operation supports it; preserve original failures |

`create_note`, `delete_reminder`, `move_note` and the other operation names in the native guide are **values inside a change proposal**, rather than standalone MCP tool names. A proposal includes the exact `operation`, selected `source_id`, relevant item/container IDs and a `fields` object. Read the returned review before execution. Specific recovery tools have narrower operation and evidence requirements; they do not provide a generic retry or clear-uncertainty action.

For the complete supported workflows, inputs, backend requirements and recovery rules, use [Notes and Reminders setup](../getting-started/native-services.md) and the [capability overview](native-support.md). The current server's MCP tool discovery supplies the schemas for the selected services; neither a schema nor this reference grants permission to use it.
