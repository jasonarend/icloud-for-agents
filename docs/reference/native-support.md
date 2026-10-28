# Notes and Reminders support

Notes and Reminders are optional development services on macOS. Each service starts disabled and has independent source selection and access controls. The MCP tools work through the shared server for stdio and private-tunnel clients. A client's ability to connect does not establish that its first-use macOS permission prompts have been tested.

| Workflow | Current implementation | Boundary |
| --- | --- | --- |
| Read today's reminders or a date range | Bounded queries and daily views, including supported filters | The agent schedules and presents briefings; the server does not deliver proactive briefings. |
| Find information in Notes | Scoped title/plaintext searches, bounded reads and exported table text | Locked notes, unavailable exports and incomplete native objects are disclosed. |
| Save a product specification or supplied meeting transcript | Reviewed note creation and supported edits; bounded plain list and table HTML formatters | These tools save supplied content. They do not record a meeting or create a native audio transcript. |
| Organize existing content | Supported folder/list renaming, task fields and completion; optional ReminderKit sections, tags and subtask operations | Container moves, arbitrary reordering and complete hierarchy edits are unavailable. |
| Inspect links in Notes | Exported HTML link pages and bounded exact-reference searches in one selected folder | No targets are opened; native backlinks and complete link identity are unverified. |
| Copy supplied observations | Explicitly reviewed lossy Notes or Reminder copies; supported single-VTODO import/export | Copies omit native metadata and can duplicate content. They are not restores or full backups. |
| Delete or restore notes, reminders or containers | No verified product operation exposed | Neither a text copy nor a native deletion primitive establishes recoverable deletion, complete effects or restoration. |
| Rich native Notes operations | Bounded exported observations where available | Native checklist editing, tag relationships, pinning, Smart Folder changes, rich object editing, sharing changes and password operations are unavailable. |

Supported writes use the existing one-time approval or explicitly scoped ongoing/scenario grants. Effective service access, selected sources, target identity, content-preservation checks and unfinished-write guards apply at execution. An OS grant does not enable application writes. A supplied observation or generated HTML does not authorize saving it.

Use `notes_access_status` or `reminders_access_status` for the current implementation inventory and unavailable-operation reasons. These local reports do not probe provider support or grant authorization. The optional private native backends have additional interface and preservation requirements; their presence does not guarantee every operation on every object.

Local synthetic tests cover protocol, approval, scope, bounds and failure behavior. Individual live observations and their limits are described in the [native service guide](../getting-started/native-services.md). Native HTML rendering, cross-device synchronization, first-use prompt attribution across clients and the complete advanced-feature set do not have blanket acceptance. A build or passing synthetic test does not establish those behaviors.

For setup, exact tool options and operation-specific evidence, use the [native service guide](../getting-started/native-services.md). Keep a failed or uncertain write's audit and inspect its retained evidence before further work; do not replay the request or clear its guard based only on an apparently unchanged object.
