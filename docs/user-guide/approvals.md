# Approvals and permissions

You choose which services to connect and what they may do. New installations start with read-only access; connecting a service or granting macOS access does not by itself authorize changes to your data.

## Reviewing a change

By default, your assistant opens an iCloud for Agents review window on your Mac for actions that require local approval. Read the proposed action and choose **Approve** or **Cancel**. The assistant can then carry out an approved action. You do not need to run Terminal commands.

You can instead choose the optional **trusted-client** policy, which relies on your assistant and its client to follow your instructions and obtain your confirmation. The server cannot independently verify that a conversation message came from you. Some actions, including contact deletion and most calendar deletion, still need local review. A separately prepared one-time Calendar deletion can use trusted-client confirmation when that policy is enabled.

Sending mail requires approval of the recipients and content. Permanent mail deletion and invitation replies are not supported. The availability of a tool does not grant permission to use it.

## One-time and ongoing access

With Calendar, Notes and Reminders enabled for read-only access, you can approve one exact change without permanently enabling ordinary writes. Notes and Reminders also support ongoing permissions for specific scenarios. You choose the allowed actions and locations, and can review or revoke that scope later. Calendar does not have scoped standing grants.

Mail cleanup has its own optional standing scope for selected folders and operations. See [standing mail cleanup](cleanup.md). A separately enabled [managed policy](policy-management.md) can provide reviewed changes to that scope and exact one-time exceptions.

Your assistant should explain any change in access before making it. An approval applies only to the request or scope you reviewed; it cannot override disabled services or the selected account and source limits. If a write times out or has an uncertain result, ask your assistant to inspect and reconcile it rather than automatically repeating it. See [troubleshooting](troubleshooting.md).

For an assistant or operator, the [approval mechanics](../reference/approval-mechanics.md) describe the policies, action-specific safeguards and recovery boundaries. The [security model](../security/model.md) explains what the local approval workflow does and does not protect against.
