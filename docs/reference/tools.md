# Tools and everyday workflows


Mail registers ten tools when selected. Calendar registers two reads and one preparation tool; Contacts registers three reads and one preparation tool. The shared DAV execution tool registers if either DAV service is enabled, and refuses plans targeting the disabled service. All three selected gives 18 schemas. Explicit service switches override the legacy combined DAV fallback. `ICLOUD_MAIL_ACCESS=read-only` permits the four mail reads/status plus five DAV reads; every mutation and write proposal is refused. Do not mistake an advertised write tool for permission or an enabled capability.

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

In a separately approved write-enabled runtime, read-state/archive/restore do not require the special local receipt. They still need human task authorization and any client confirmation. Send and Trash require appropriate task authorization and the selected strict receipt, trusted-client or standing-cleanup path; see [approval policies](../user-guide/approvals.md). Standing cleanup refuses stars and out-of-scope folders before ordinary receipt handling. Do not enable writes as an incidental setup repair.

## Review a mailbox without skipping older mail

Start by listing folders rather than guessing English names such as Archive or Trash. Use one exact selectable name with `email_page`; the default page size is 50 and the permitted range is 1–100. Optional sender/subject/unread filters bind the cursor to that query.

Save the returned `next_cursor` only after reviewing or recording the page's outcome. Continue even when a page contains no messages, until `done=true`. A call searches at most 2,000 numeric UID positions and fetches at most 100 bounded headers, so an empty UID window does not mean the mailbox is finished.

Each scan holds a fixed high-UID ceiling. New arrivals are not silently inserted ahead of older backlog. Save `completed_snapshot` on completion. A later incremental scan uses that snapshot's `high_uid` as `after_uid` and its `uidvalidity` as `expected_uidvalidity`. If validity, account, folder, or filters change, rediscover/restart instead of reusing identities. UIDs are not stable across different folders or UIDVALIDITY values.

Other mail clients can still move messages or change flags while a scan is running. An unread-only scan is not a full-folder audit; an unfiltered backlog pass is more robust against changing UNSEEN membership. A vanished or failed-to-parse item remains an explicit exception to reconcile, not proof it was reviewed.

Inspection fetches at most 256 KiB with BODY.PEEK and returns at most 50,000 text characters. Plain MIME text is preferred; HTML-only mail gets bounded static HTML-to-text extraction. No browser rendering, link/image fetch, script execution or attachment inspection occurs. Recognized hidden elements/styles and transparent tracking footers are excluded. `body_format` identifies plain, html_to_text or none; `html_only_or_no_plaintext` remains true for an HTML fallback and does not imply an empty body. `truncated` includes wire, decoded output and structural bounds; `mime_defects_detected` flags malformed input. Complex CSS/image-only content may still yield an incomplete or blank view. All extracted content remains untrusted. Do not discard mail because its bounded preview has no readable body.

## Review and execute a send or Trash batch

This strict-mode sequence applies only after write access and that action are approved. Preparing a proposal does not authorize or execute it. Explicitly enabled trusted-client mode relies on client authorization instead of the second Terminal step; it is not proof of independently authenticated human approval.

1. Prepare exact recipients/content or exact Trash message identities and reason. Review the returned proposal and its plan ID.
2. The human personally runs `icloud-agents-approve PLAN_ID` in a trusted local Terminal. The command displays escaped details and a hash, then asks for the exact `SEND PLAN_ID` or `TRASH PLAN_ID` phrase. A general yes is not a receipt.
3. Call `email_execute_approved` with that same plan ID. The receipt binds the proposal, account, and 30-minute proposal expiry. It cannot authorize another batch, edited recipient, or future action.
4. After interruption or an uncertain result, inspect `email_plan_status` before doing anything else. Never resend/re-trash by replaying the plan or creating an automatic replacement.

The assistant must not run the approval CLI, synthesize the typed phrase, or write receipt files. Read-only mode refuses this sequence even if a receipt exists; a local approval does not override the launch mode.

Successful moves return destination UIDVALIDITY and source-to-destination UID mappings. Use destination identities when inspecting or restoring. A failed MOVE verification may occur after the server moved mail, and a lost SMTP response may follow delivery. Local replay protection does not prove an external action never happened. Reconcile source/destination or Sent and, when appropriate, the recipient before authorizing a replacement.

Trash is recoverable only while the mail provider retains it. There is no empty-Trash or permanent-purge tool. Sending supports bare explicit email addresses, at most 50 combined recipients, a single-line subject up to 998 characters, and a plain-text UTF-8 body up to 100,000 bytes. Bcc recipients are not placed in the sent message headers.

## Calendar and contact tools

Select Calendar with `ICLOUD_ENABLE_CALENDAR=1` and Contacts with `ICLOUD_ENABLE_CONTACTS=1`. The combined `ICLOUD_ENABLE_DAV_READ=1` remains a fallback only when explicit switches are absent. It does not override runtime read-only access. Credential-free stdio tests exercise this boundary with synthetic fixtures.

| Tool | Behavior |
| --- | --- |
| `calendar_list` | Discover exact calendar collections |
| `calendar_events` | Bounded raw events, timezone-aware range <=31 days, <=200 resources |
| `contacts_addressbooks` | Discover exact addressbooks |
| `contacts_list` | Metadata pages of 1–100 references; bounded live listing, not an immutable snapshot |
| `contacts_get` | One bounded raw vCard, <=256 KiB |
| `calendar_prepare_changes` | Prepare 1–20 explicit personal-event create/update/delete operations; no writes yet |
| `contacts_prepare_changes` | Prepare 1–20 explicit contact create/update/delete operations; no writes yet |
| `dav_execute_approved` | Execute an exact calendar/contact plan under the selected approval policy |

The three change tools are refused in initial read-only mode. A separately authorized `write-enabled` runtime and the selected approval policy are both necessary; general irreversible deletion retains strict local receipt handling. Read-only is the validation stage, not a permanent product restriction.

Each update/deletion binds a strong ETag and private before-content snapshot; conditional If-Match protects against drift. Create uses an assigned resource URL and If-None-Match: *. One exact approval covers the bounded batch, not a new question per item. Interrupted or partially completed batches are never retried/rolled back automatically. Check exact resources and completed outcomes before a replacement plan.

DAV has no protocol Trash here. Deletion requires `irreversible_confirmed: true` plus the human's explicit `IRREVERSIBLE DELETE ...` phrase; the snapshot is retained privately for review/manual recovery, not a promise of server undo. Prefer update over deletion when possible. Provider-native recovery availability is not verified.

Personal single-event changes support summary/start/end/description/location/timezone/url/free/private. Named IANA timezone is validated against explicit offsets; UTC timestamps plus X-ICLOUD-MCP-TIMEZONE metadata are stored, not a native VTIMEZONE component. Recurring-series changes and any event with ATTENDEE, ORGANIZER, or METHOD are refused: invitations, scheduling responses, cancellations, and external emails require a separate explicit workflow and are not silently sent. Contact fields support full_name, emails, phones, organization, and note; unknown existing contact properties/UID and nested calendar alarms are retained. No iCloud Drive or contract-file access is implemented.
