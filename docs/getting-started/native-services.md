# Native Notes and Reminders development setup

Current implementation is a read-only development increment, not full service support. Native Notes and Reminders are independently disabled by default. Existing Mail/Calendar/Contacts configuration and approval modes are preserved.

**Current capabilities**

Reminders exposes OS permission status, source discovery, selected-source list discovery, bounded task pages, date/timezone-filtered daily queries and exact task reads through public EventKit. Notes exposes account discovery, selected-account folder discovery, bounded note-title pages and unlocked note text/HTML reads through built-in JXA automation. Advanced native metadata is explicitly incomplete. Offset pages may shift during external edits. Access-status tools report effective service scope and approval policy without contacting Apple. No native write tools are implemented in this increment; the shared approval executor has synthetic coverage but no registered live write adapter.

**Owner-controlled setup**

An authorized agent can build the repository-owned helper with `scripts/build-native-helper.py`, using a new output directory for each reviewed build. Swift and codesign are macOS build-time tools; neither a separate MCP server nor an additional Python runtime dependency is required. The resulting app is ad-hoc signed for development. A release still needs its own signing/distribution and upgrade-permission continuity evidence.

After the owner selects a service, private settings accept `native_services` alongside the optional `notes` and `reminders` service booleans. For example, these nonsecret fields illustrate the schema; they do not enable a live installation:

```json
{
  "native_services": {
    "notes": {"access_mode": "read-only", "source_ids": []},
    "reminders": {
      "access_mode": "read-only",
      "source_ids": [],
      "helper_path": "/absolute/path/to/iCloudAgentsHelper.app/Contents/MacOS/icloud-agents-native"
    }
  }
}
```

Empty source allowlists permit source metadata discovery only. Select exact returned IDs under owner authorization before listing containers or reading content. Do not infer native source identity from the configured IMAP login or account display name. The launcher never forwards inherited native opt-ins or native configuration.

The helper accepts protocol-versioned JSON on stdin. Normal MCP reads cannot request a grant. An explicit local `request_access` helper action asks macOS for Reminders full access; the human handles the system dialog. That OS grant never enables application writes. Notes automation can cause macOS to ask for permission when explicitly selected source discovery is first attempted. Permission attribution depends on the launch host and must be validated per client. Do not reset privacy databases or bypass a denied prompt.

The agent handles supported setup steps; terminal commands remain an optional owner/operator path, consistent with existing services. Existing strict receipt and trusted-client workflows are not replaced by native OS permission grants.

**Validation status**

Offline tests cover independent service selection, stale-tool refusal, missing scope, disabled access, input bounds, fixed JSON dispatch, environment filtering and response failures. A compiled helper status check is native execution evidence only; full access, content reads and cross-device sync require their own live acceptance. Rich fixtures and write testing must wait for read-only success in both services and the separate write-access approval gate.
