# Managing cleanup policies

The optional managed-policy feature lets your assistant propose changes to an existing standing mail-cleanup scope. It is disabled by default and requires your approval to enable. Enabling it does not authorize a specific message action or give it additional Apple access.

Once enabled, your assistant can show you a before-and-after proposal for a policy change, such as different folders, operations or limits. It can also propose an exact one-time exception for moving selected protected messages to recoverable Trash. Your chosen approval policy applies to each request. A one-time exception does not change the standing policy.

The original cleanup grant must still be valid. Managed policies cannot authorize sending mail, calendar or contact changes, permanent deletion, or security changes. They also do not create separate user accounts: clients permitted to use the instance share its configured account and policy boundaries. Individual HTTP client permissions still apply.

You can ask your assistant to inspect the active policy or revoke an unstarted proposal. An action already sent to the provider cannot be recalled. If an action's result is uncertain, it must be inspected before further work rather than repeated automatically.

For activation, tool inputs and enforcement details, an assistant can use the [managed-policy reference](../reference/managed-policies.md). See [approvals](approvals.md) for local review and trusted-client options.
