# Security reporting

**Report privately**

Use [GitHub private vulnerability reporting](https://github.com/jasonarend/icloud-for-agents/security/advisories/new) for suspected vulnerabilities. From the repository, open Security → Advisories → Report a vulnerability. This sends a private report to the repository maintainers rather than creating a public issue.

The repository owner must enable private vulnerability reporting before the first public release and verify that the report form is available. Until the repository is published and that setting is enabled, this route is not active. If the form is unavailable, open a public issue requesting a private reporting channel without including vulnerability details, credentials or private data; wait for a private channel before sharing the report.

Include the project version or commit, macOS/Python/client versions, affected feature, prerequisites, expected and observed behavior, and a minimal reproduction using synthetic data. Describe the possible impact and any suggested correction. Never include app-specific passwords, API keys, real messages, contacts, calendar contents, approval files or unredacted runtime logs. Do not test against someone else's account.

**Versions and response**

Please reproduce against the latest release or current main branch where practical. Security fixes target the latest release; older releases have no separate maintenance commitment. Review is best effort, with no guaranteed response time. If a report receives no acknowledgement after seven days, use a public issue only to request follow-up, keeping the technical details private.

Coordinate disclosure with the maintainers so users can receive a correction before exploit details are published. A report is not permission to access private accounts, extract data or disrupt services.

**Security model**

Read the [security and consent model](docs/security/model.md) for the distinction between read-only access, local approval receipts, trusted-client authorization and same-user process authority. Locally stored credentials do not mean retrieved content stays on the Mac; content is returned to the connected AI client and its provider.
