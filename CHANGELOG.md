# Changelog

Releases are listed newest first. Dates are the local dates on which the GitHub releases were published.

## 2.2.1 — 2026-11-12

### Added

- Connect compatible MCP clients over an optional authenticated Streamable HTTP endpoint, including Meta Muse and custom-built agents. The server listens on the Mac; remote clients also need a separately configured HTTPS connection.
- Create separate client keys with reviewed access for each Apple service and optional client-management authority. Review keys locally, transfer them through a secure handoff, and change, rotate or revoke them without restarting the server.
- Let HTTP clients request a review of their own permissions and prepare individually approved Calendar, Notes and Reminders changes within the selected service scope.
- Manage the optional HTTP server as a separately reviewed background service. Add setup guidance for private Tailscale connections and an optional Cloudflare Tunnel launcher.
- Include a server icon and expanded MCP identity metadata for clients that display them.

### Improved

- Keep durable records for supported Mail read-state changes, archive and restore operations. Inspect uncertain outcomes and review recovery without automatically repeating a write.
- Coordinate supported Mail and other operations when local and HTTP clients share an installation, and improve HTTP service recovery after a rapid restart.
- Accept the reviewed HTTPS proxy origin while continuing to reject unrelated browser origins.
- Update the setup, security and contributor guides, including assistant-led Meta Muse setup and technical references for HTTP connections and native services.

### Fixed

- Accept valid Cloudflare Tunnel token secrets while still verifying the reviewed tunnel identity.

## 2.1.2 — 2026-11-08

### Fixed

- Wait for macOS to finish stopping the login service before reporting success. If shutdown cannot be verified, preserve the installation rather than removing it prematurely.

## 2.1.1 — 2026-11-08

### Improved

- Automatically select a matching macOS compiler and SDK when building the native helper, including on Macs with both Xcode and Command Line Tools installed.
- Launch the Reminders helper as a macOS app so permission requests use its app identity.
- Add setup guidance for Unsloth Desktop and Hermes, and clarify client and native-service setup.
- Add release checks that help contributors keep version references consistent before pushing changes.

### Fixed

- Return a verified Reminders read result when the helper has already responded but macOS subsequently reports a launcher error, with a warning for the assistant to inspect.
- Correct the documented ChatGPT tool count.

## 2.1.0 — 2026-11-05

### Added

- Read recurring Calendar occurrences within a selected date range and create, update, or delete supported personal event series, including daily, weekly, monthly, and yearly patterns.
- Change or remove a supported individual occurrence, including supported local-time moves and existing exceptions.
- Change or remove selected future occurrences. Supported schedule changes use a reviewed series split, with partial failures reported for review.
- Open a local review window for strict approvals, so an owner can approve an exact proposal without using Terminal.
- Set up and revoke scoped standing Mail cleanup with an assistant, including checks that activation succeeded.

### Improved

- Preserve supported event alarms and existing exceptions during applicable recurring-event changes.

## 2.0.1 — 2026-11-03

### Fixed

- Stabilize native permission checks on GitHub Actions and allow more time for the source-check workflow. This release does not change the app's runtime behavior.

## 2.0.0 — 2026-11-03

### Added

- Connect Apple Notes and Reminders independently, with separate source selection and access controls. Both start disabled.
- Browse and search notes, read supported text and Markdown views, save supplied content, and make supported edits. Create folders and move eligible notes between them.
- Read reminders by date and other supported filters; create and edit tasks, manage due dates and supported alerts, and organize tasks with supported lists, sections, groups, tags, flags, and subtasks.
- Work with supported Reminder repeat rules, custom Smart Lists, grocery categories, and simple saved templates.
- Recoverably remove and restore eligible notes and reminders, with checks specific to each operation.
- Inspect and export supported attachments, read exported Notes tables, and extract printed English text from supported images.
- Approve individual Notes and Reminders changes or establish explicitly scoped ongoing permissions. Safeguards retain evidence and stop unsafe retries when a write has an uncertain outcome.
- Add a bundled macOS helper and setup guidance for the permissions needed by selected native features. Some advanced features require an optional native backend and additional macOS access; see the [Notes and Reminders capability reference](docs/reference/native-support.md) for supported operations and limits.

### Improved

- Declare MCP safety annotations for registered tools so compatible clients can identify their intended behavior.

## 1.1.0 — 2026-10-27

### Added

- Connect an assistant to iCloud Mail, Calendar, and Contacts. Choose services independently and start with read-only access.
- Browse and search Mail without marking messages read; send approved messages, change read state, archive, move messages to recoverable Trash, and restore them.
- Read calendars and contacts and prepare approved changes to supported personal events and contact records.
- Connect compatible local MCP clients, including Claude Desktop and Claude Code, or use the private OpenAI tunnel for ChatGPT.
- Configure optional, scoped Mail cleanup and managed policies, with previews, item-specific exceptions, revocation, and an audit trail.
- Manage background startup with verified login-service status, stop, and uninstall commands. Support an explicitly selected tunnel executable.
- Document installation maintenance, data retention, and private vulnerability reporting through GitHub.

### Fixed and improved

- Verify that messages have disappeared from their source after a move and reached the requested read state. Stop unattended cleanup when an outcome is uncertain or protected message state has changed.
- Correct shared credential handoff across Mail, Calendar, and Contacts regardless of initialization order.
- Correct updates to grouped contact email and phone fields, and remove conflicting event duration data when an explicit end time is set while preserving nested alarms.
- Complete source distributions and dependency notices, add macOS source checks, and support verified portable dependency wheels on older macOS installations.

## Earlier development

The original changelog records initial version 1.0.0 development on October 20, 2026, for a local Mail server with approval-gated changes and a private ChatGPT tunnel. This repository has no 1.0.0 Git tag or GitHub release; 1.1.0 was its first published release.
