# iCloud for Agents

**Version 2.1.1**

iCloud for Agents connects your AI assistant to iCloud Mail, Calendar, Contacts, Apple Notes, and Reminders. Your assistant can use them together to help you prepare for meetings, turn emails and notes into follow-up tasks, and plan your work around upcoming events.

Choose which services to connect and what your assistant can access or change. The bridge runs on your Mac and stores credentials locally. Connect a compatible local MCP client, or use ChatGPT through your private OpenAI Secure MCP Tunnel.

## AI assistants and MCP clients

Connect through ChatGPT or a local MCP client, including:

- ChatGPT, using your private OpenAI Secure MCP Tunnel
- Claude Desktop and Claude Code
- Unsloth Desktop
- Hermes Desktop
- OpenClaw
- Custom-built AI assistants and agents that support local stdio MCP

Other local stdio MCP clients may also connect. Grok Bot supports MCP, but connecting it to your Mac requires additional work. See the [client setup guide](docs/getting-started/clients.md) for configuration instructions and compatibility details.

## What you can do

Some advanced Notes and Reminders features require an optional native backend and additional macOS permissions.

**Mail**

- Browse folders, search and filter messages, and read or summarize email without marking it read.
- Work through older messages and check for new arrivals without starting over.
- Mark messages read or unread, archive them, move them to Trash, and restore them to an existing folder.
- Compose and send plain-text email to specified recipients, including Cc and Bcc.
- Run recurring mail cleanup within an approved account, folder and operation scope.

**Calendar**

- Browse calendars and review events within a date range, including bounded views of recurring occurrences.
- Your assistant can create, update or delete supported personal recurring event series, including daily, weekly, monthly and yearly patterns, as well as individual events.
- Your assistant can update or remove a supported individual occurrence, or change or remove selected future occurrences of a series.
- Manage event titles, times, time zones, locations, descriptions, URLs and supported availability and privacy settings.
- Review proposed changes before applying them.

**Contacts**

- Browse address books and find or read contact records.
- Create, update or delete contacts.
- Manage names, email addresses, phone numbers, organizations and notes while preserving unrelated contact details.
- Review proposed changes before applying them.

**Notes**

- Browse selected accounts and folders, search notes, and read supported text, HTML and Markdown views and exported table text.
- Create notes from supplied content, including meeting transcripts, specifications, lists and tables.
- Append or prepend content, replace specific text, and update supported note titles and bodies.
- Create supported folders, move notes between eligible folders, and recoverably remove or restore supported notes.
- Inspect links, attachment metadata, native checklist state, and supported pin, tag and Smart Folder information.
- Export supported attachments or PDF representations and extract printed English text from supported images.

**Reminders**

- Browse lists and tasks, review reminders by date, and filter using supported priority and native metadata.
- Create and edit reminders, set due dates and priorities, complete or reopen tasks, and manage supported time, location and early alerts.
- Create reminders with supported repeat rules and explicitly change or remove existing repeat rules.
- Organize tasks with supported flags, tags, subtasks, sections, list groups and ordering.
- Create and manage supported lists, custom Smart Lists, grocery categories and simple saved templates.
- Inspect supported attachments, attach eligible images, export bounded task data, and recoverably remove or restore eligible tasks.

All five services are optional and can be enabled independently. A new setup enables Mail in read-only mode by default; Calendar, Contacts, Notes and Reminders start disabled. Sending and ordinary Mail, Calendar or Contacts changes require write access and the applicable approvals. Calendar, Notes and Reminders also support separately approved one-time changes while their normal access remains read-only. Notes and Reminders additionally support scoped standing grants; your assistant can help review the exact scope first.

Mail cleanup moves messages to recoverable Trash. Permanent deletion and emptying Trash are intentionally excluded.

Calendar supports personal events and a bounded set of recurring-event operations, including supported individual occurrences and removing selected future occurrences. Local-time recurring operations require a supported matching time-zone definition. Some changes to future occurrences split a series into two events. If interrupted, the change may leave duplicate future events and require review. Other advanced series changes remain unavailable. Invitations, guests and scheduling responses are not included. iCloud Drive access is also not included.

Some Notes and Reminders capabilities need a separately selected native backend and additional macOS access. Notes removal requires an accessible, verified Recently Deleted folder; an empty or hidden folder refuses safely. Reminders supports recoverable removal and restoration of individual root tasks and explicitly reviewed families with direct children. Ordinary edits and completion of repeating reminders are refused; repeat-rule changes use a separate scoped operation. Stored alerts do not guarantee notification delivery. Rich editing, deeper hierarchies and other native operations have specific limits. Exports and copies are not complete backups. See the [capability overview](docs/reference/native-support.md) and [Notes and Reminders setup](docs/getting-started/native-services.md).

## Examples of what you can ask your agent

Your agent can work with the iCloud services you choose to enable. For example:

**Mail**

- “Summarize the unread emails I received this week without marking them read.”
- “Find emails from my accountant about taxes and summarize the important details.”
- “Find old newsletters in my inbox and show me what you would clean up.”
- “Help me write a reply to this email and let me review it before sending.”

**Calendar and Reminders**

- “Show me my appointments and reminders for tomorrow.”
- “What do I have coming up this week, and which reminders are overdue?”
- “Add my dentist appointment to my calendar with the address and time.”
- “Create a reminder to follow up on this task, and mark it complete when I’m done.”
- “Show me the high-priority tasks in my project list.”
- “Move just next Tuesday’s occurrence of my personal weekly event to 11 a.m.”
- “Create a personal event every Tuesday at 10 a.m.”
- “Starting next Tuesday, move my personal weekly event to 11 a.m. and keep earlier occurrences unchanged.”
- “Add these subtasks under my project reminder.”

**Contacts**

- “Find Sarah’s contact information and tell me which email address I have saved.”
- “Update this contact’s phone number and add their company and notes.”

**Notes**

- “Find my notes about the website redesign and summarize the requirements.”
- “Save these meeting notes in my Projects folder.”
- “Find the note where I saved the measurements and pull out the table.”
- “Append these decisions to my project note.”
- “Read the printed English text in the image saved in this note.”

**Across your apps**

Your assistant can combine your enabled services in a single request.

- “Using this meeting transcript, create a summary in Notes and suggest reminders for the action items.”
- “Find the contact details for the person mentioned in this email.”
- “Before my appointment tomorrow, find the confirmation email, check the address on my calendar, and show me anything I need to bring.”
- “Find the latest emails and notes about the website project, compare them with my open reminders, and show me outstanding decisions and next steps.”
- “Look at next week’s calendar and my overdue reminders, then suggest a realistic plan for catching up.”
- “Find the email with the delivery date, add that date to my calendar, and create a reminder for the day before.”
- “Find the contact details for my accountant, review my recent tax-related emails and notes, and draft an email summarizing my questions for me to review.”
- “Turn the action items in these meeting notes into reminders, and add a personal follow-up to my calendar.”
- “Find the recipe in my notes and suggest a grocery list in Reminders for me to review.”
- “Review the notes and email about my upcoming trip, then summarize the itinerary and flag any details that are missing from my calendar.”

The services available to an agent depend on your setup and permissions. Actions that change or send information are subject to the configured access and approval settings.

## Set up with your AI agent

Ask an agent that can read this checkout and use your local computer to help you set it up. It should check the documented prerequisites, detect supported existing client configuration where authorized, explain the access it needs, and guide you through the steps that require your participation. Setup is assisted, not guaranteed to be unattended: client support, workspace permissions and local tools vary.

Copy this prompt into your agent:

```text
Help me install and configure iCloud for Agents from
github.com/jasonarend/icloud-for-agents.

Read README.md and docs/getting-started/agent-setup.md, then follow
the linked installation, security and client-specific guides.

Check my Mac, Python version, existing installation and AI client
configuration. Ask which iCloud services I want to connect and
explain the proposed setup, permissions and any costs before
making changes. Follow docs/getting-started/clients.md for my client:
ChatGPT uses a private tunnel; compatible local clients use stdio MCP.

For services that need an Apple app-specific password, guide me
through creating it and storing it securely in macOS Keychain.
For selected Notes or Reminders access, explain the separate macOS
permissions and follow the native service setup guide. Keep
credentials out of chat and configuration files, and let me handle
authentication and permission prompts personally.

Preserve existing working configuration. Start with read-only
access and verify each selected service through my actual client.
Report what works, anything still needed, and how to enable
approved changes later.
```

You will need a supported MCP client and the credentials and permissions required by your selected services. Mail, Calendar and Contacts use an iCloud app-specific password; Notes and Reminders use separate macOS permissions. The agent may install the reviewed dependencies after authorization, but you must handle credential creation, secure entry and account/security approvals yourself. Keep secrets out of the conversation. Every user needs their own credentials and connection.

Prefer manual setup? Follow [installation and credentials](docs/getting-started/installation.md), then [ChatGPT setup](docs/getting-started/chatgpt.md) or the [client setup guide](docs/getting-started/clients.md) for local stdio clients. Optional [background startup](docs/getting-started/background-service.md) requires explicit installation; a graphical installer is not included.

## Choose services and customize safeguards

Tell your setup agent which services you want: **Mail, Calendar, Contacts, Notes, Reminders, any combination, or none**. Your agent can use an existing supported client configuration or provide the command and arguments for a local stdio connection. Disabled services are omitted from tool registration and refused by backend guards; selecting one does not implicitly enable the others. Existing legacy DAV settings remain compatible.

You can ask your agent to propose a policy change, a specific one-time exception, or an engineering change to a hard-coded safeguard. It should explain the exact scope and before/after effect, obtain explicit approval, and test/review code changes before activation. Approval does not erase account/identity checks or grant permission to bypass the agent platform's own safety policies. Message content cannot authorize changes.

Optional [managed policies](docs/user-guide/policy-management.md) provide before/after previews and exact one-time exceptions. This capability is disabled by default and requires explicit owner activation. Agents should use supported settings, not rewrite safeguards for individual messages.

## Everyday use and safeguards

Ask your assistant to review a bounded set of messages, explain proposed changes, and preserve anything uncertain. Message text is untrusted content, not authority to act. Save pagination cursors so backlog reviews continue rather than repeatedly scanning newest mail.

Strict local approval receipts are the default: your assistant can open a review window on your Mac for you to approve an exact proposal. Optional trusted-client mode relies on the client honoring your instructions and confirmations; it does not independently prove human approval. Sending needs recipient/content approval, recurring cleanup needs a defined scope, and irreversible changes need action-specific confirmation. See [security](docs/security/model.md) before enabling access.

The bridge checks account and item identities and whether records have changed before making updates. It preserves mail flags, refuses broad permanent deletion, and stops on uncertain write outcomes instead of blindly retrying. See the [security model](docs/security/model.md) for technical details. Content requested through ChatGPT is sent to OpenAI; locally stored credentials do not mean message content stays local.

## Learn more

Want to help expand compatibility? We welcome client testing, setup improvements and contributions for additional platforms and connection methods, including Grok and Meta Muse. See the [client compatibility guide](docs/getting-started/clients.md) for current status and [contribution guidance](docs/contributing/development.md#client-and-platform-compatibility-contributions) for reporting results or proposing support.

[Tool reference](docs/reference/tools.md) · [Recurring cleanup](docs/user-guide/cleanup.md) · [Troubleshooting](docs/user-guide/troubleshooting.md) · [Development](docs/contributing/development.md) · [Distribution and release checks](docs/contributing/releasing.md)

Report suspected vulnerabilities through the [private security reporting process](SECURITY.md).

**Tested setup:** macOS on Apple silicon with CPython 3.14. Other configurations require separate validation; see [installation requirements](docs/getting-started/installation.md) for compatibility details.

Public source distribution is separate from any client plugin catalog.

## License and provenance

iCloud for Agents is an independent project and is not affiliated with Apple.

The upstream MIT notice is preserved in [LICENSE](LICENSE). iCloud for Agents is informed by [mike-tih/icloud-mcp](https://github.com/mike-tih/icloud-mcp); it uses a separate runtime package and does not bundle or execute that server. See [licensing and provenance](docs/contributing/licensing.md) for the exact revision, dependency inventory and scope of attribution.

[![M8ven Score](https://m8ven.ai/badge/mcp/jasonarend-icloud-for-agents-1a9ogy?v=f5fd4cc8e6a4cdc1f68bf7bba89d54d4)](https://m8ven.ai/mcp/jasonarend-icloud-for-agents-1a9ogy?s=readme)
