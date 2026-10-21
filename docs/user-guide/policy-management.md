# Managed policies and exact exceptions

Managed policy tools are **disabled by default**. When explicitly enabled for a single-account mail instance, they let an agent prepare a change, explain the exact effect and execute it under the owner's selected approval policy. They do not add Apple credentials or bypass the agent platform's own safety rules.

## Standing changes versus one-time exceptions

| Request | Result |
| --- | --- |
| Change folders, operations, quota or expiry | An immutable before/after policy preview; approval applies one exact revision |
| Trash specific starred items or items in another source folder | A separate item-bound proposal; approval covers only that snapshot and expires within five minutes |
| Revoke a pending proposal | Prevents admission if it has not started; cannot recall an in-flight provider action |

The original owner-installed base cleanup grant must remain valid and unrevoked. Its lifetime cannot be extended by a managed revision. Ordinary cleanup uses the managed scope while retaining starred-message protection. An exception never rewrites that standing rule, and a standing refusal never automatically falls back to an exception.

All exceptions are **recoverable Trash only**: exact account, original plan hash, source folder, UIDVALIDITY, UID set, snapshot, destination and explicit deviations. Current identity and flags are checked again immediately before execution. Flags/stars are preserved. Sends, DAV changes, purge, credential/security changes, wildcard scope and batches over 100 are refused. Semantic categories remain the agent's written task rules, not server-verified classifications.

## Approval modes

**Strict local receipt** is the default. The human reviews the exact proposal and confirms it through:

```sh
.venv/bin/python scripts/approve-policy.py PROPOSAL_ID
```

The command opens the local TTY, displays escaped proposal details and requires the exact proposal ID/hash phrase. An agent must not run the human approval step or forge the receipt. This is a local-owner workflow boundary, not a cryptographic defense against another process with the same OS identity.

**Trusted-client mode** deliberately relies on the connected client to enforce the owner's chat instructions and confirmations. Execution takes the exact proposal ID/hash and an authorization reference for audit. That reference is a **client assertion**, not independently authenticated owner consent. There is no model-supplied approval boolean or remote receipt-minting tool. Clients sharing this instance share its configured authority; there is no per-client user isolation.

## Tools

- `policy_status`: read effective revision, hash and approval mode; optionally inspect an exact proposal outcome without retrying it.
- `policy_prepare_change`: preview exact persistent mail-policy changes.
- `policy_prepare_exception`: preview one exception to an existing Trash plan, naming only the necessary `starred` and/or `source_folder` deviations.
- `policy_execute_approved`: apply the exact reviewed policy revision or execute the exact approved Trash batch once.
- `policy_revoke_proposal`: revoke an unstarted proposal.

Management, mail-admission and cleanup-ledger locks serialize updates and actions. Stale policy/snapshot, altered hash, expiry, replay, revoked authority, aggregate limits and unresolved outcomes stop execution. Results are durably recorded across policy, mail-plan and cleanup audit stores. Partial writes are not automatically retried or treated as success.

## Explicit activation

Activation is a separate persistent-access decision. It would allow the connected client to request persistent mail-policy edits and one-use exceptions for previously protected starred/source-folder batches. Approving a particular later proposal still does not authorize other targets.

1. Review the existing account, base grant, selected services and strict/trusted approval mode. Enable Mail and separately authorize write capability first.
2. After explicit owner approval, initialize a separate managed-policy workspace from that base grant using `scripts/initialize-policy.py --initialize`. The initializer creates no credentials and does not enable the feature.
3. Deliberately set `policy_management_enabled: true` in the owner-only settings and reload the idle instance once. No inherited client environment can opt in through the validated wrapper. Refresh client tools through its supported controls.
4. Verify status and a synthetic/read-only workflow before considering separately approved real mutations. Base-grant revocation remains effective for every managed action.

Without activation the five tools are absent and direct managed execution is refused. A missing, invalid, expired or revoked managed profile fails closed; it does not silently fall back to the original grant. Disabling the feature intentionally returns to the separately installed base grant, so review that scope before changing the switch.

## Customizing hard-coded safeguards

Configured policy changes use this interface. Altering hard technical boundaries requires a separate engineering proposal explaining risk, regression tests and review before activation. External content cannot authorize either kind of change. Independent remote owner authentication, a browser approval UI, HTTP MCP hosting and multi-user policy isolation are not provided.
