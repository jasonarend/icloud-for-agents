# Set up Notes and Reminders

You can connect Notes, Reminders, both, or neither. Each has its own account selection and access controls. A local assistant can handle setup and guide you through the macOS permission requests; you do not need to run Terminal commands.

For example, tell your assistant: “Connect Notes and Reminders, let me choose the accounts, and start with read-only access.”

## Connect the services

1. Your assistant prepares the bundled iCloud for Agents helper and enables the services you choose. It preserves your existing installation and starts newly enabled services in read-only mode.
2. Review each request in the iCloud for Agents window, then respond to the macOS permission dialog yourself. Notes and Reminders require separate permissions. The macOS Reminders grant may describe broader access than the app's selected accounts and permissions.
3. Choose the accounts you want to make available. Until accounts are selected, the assistant can discover available accounts but cannot read their folders or lists.
4. Your assistant applies the selection, reconnects the affected client and checks a small page of folders or lists. You can then ask it to read the information you choose.

The helper is part of the project, not a separate product. Notes normally uses macOS Automation permission; Reminders uses its own macOS permission. Normal Notes setup does not require Full Disk Access or Accessibility. Some advanced features need an optional backend and additional access, which your assistant should explain before enabling it.

## Choose what changes are allowed

You can ask for a single change, such as creating a reminder or editing a note, and review that exact request. You can also approve an ongoing scope for a specific scenario, such as adding meeting summaries to a selected folder. A scenario names the permitted actions and locations; you can review, change or revoke it later. Your chosen approval policy still applies. See [approvals](../user-guide/approvals.md).

Notes and Reminders can also organize and, where supported, remove or restore items. Some operations have specific account, folder, item or format requirements. Your assistant should check the requested action and explain any limitation before proposing it. Copies and exports are not complete backups. The [capability overview](../reference/native-support.md) and [technical operations reference](../reference/native-operations.md) describe the available tools and their boundaries.

## If access or a change fails

Ask your assistant to check the configured helper and the service's status. For Notes, check the helper's entry under **System Settings → Privacy & Security → Automation**. If Notes is unresponsive, quit and reopen it normally. An enabled macOS switch alone does not confirm that the current helper can read the service.

If a change times out or its outcome is uncertain, do not repeat it automatically: the app may have made the change. Ask your assistant to inspect the result and use the applicable recovery workflow. See [troubleshooting](../user-guide/troubleshooting.md).

For setup commands, account selection, operation-specific requirements and recovery details, an assistant can use the [technical operations reference](../reference/native-operations.md).
