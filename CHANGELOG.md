# Changelog

## Unreleased

- Include every test runner and fixture, plus the security policy, in source distributions; add an archive completeness check.
- Reuse one inherited credential handoff across Mail and DAV in either initialization order.
- Replace and clear grouped vCard properties; remove event DURATION when setting an explicit end while preserving nested alarms.
- Verify the final read/unread state of every requested message; uncertain cleanup results block further unattended actions.
- Allow an explicit owner-selected tunnel executable path and document installation/version checks.
- Add verified login-service status, stop and uninstall commands without credential lookup.
- Document GitHub private vulnerability reporting and require activation before the first public release.

## 1.1.0 — 2026-10-21

- iCloud for Agents package, command-line tools and documentation.
- Independent Mail, Calendar and Contacts selection with disabled-service guards.
- Client-neutral stdio setup and an optional private OpenAI tunnel adapter.
- Nonmarking mail inspection, bounded UID traversal, recoverable Trash and verified moves.
- Exact calendar/contact proposals with conditional updates and strict irreversible-delete approval.
- Optional managed mail policies with before/after previews, one-time item-bound exceptions, revocation and durable audit. Disabled by default.
- Credential-free regression tests, production-adapter fixtures and real stdio protocol tests.

## 1.0.0 — 2026-10-20

- Initial local stdio MCP server for iCloud Mail, with exact-folder discovery, bounded oldest-first pagination and nonmarking message inspection.
- Private OpenAI Secure MCP Tunnel setup for connecting the local server to ChatGPT without a public MCP endpoint.
- Read-only defaults, local credential handoff and approval-gated Mail change proposals; permanent mail purging disabled.
- Isolated macOS environment, hash-locked dependencies and credential-free safety/protocol tests.
