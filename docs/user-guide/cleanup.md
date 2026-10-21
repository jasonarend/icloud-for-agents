# Standing mail cleanup (inactive by default)

A reviewed standing scope binds account, exact source/destination folders, allowed archive/mark-read/recoverable-Trash operations and safe per-operation batch size. It never covers sending, DAV changes, restore, mark-unread, invitations, permanent purge or security changes. Categories belong in the owner's recurring task; the server cannot semantically verify importance or junk classification.

Aggregate quota and expiry are owner choices: `max_24h: null` means no daily cap, `days: null` in the request becomes `expires: null` (no autoexpiry). Positive quotas and durations are supported. Multiple runs each day are supported regardless; a per-batch bound is not a daily allowance. No grant is active by default.

The existing owner review CLI remains an optional strict deployment path, not a requirement for chat-approved ordinary actions. See [Approval policies](../user-guide/approvals.md) for opt-in trusted-client semantics and their honest trust boundary. Do not silently change existing owner settings or generate approvals. Activation requires explicit owner consent.

Scope is checked before each batch and under the audit lock. Exact UIDVALIDITY, snapshots, flags, safe MOVE/UIDPLUS, durable started/result records, replay prevention, uncertain-outcome blocking and revocation remain enforced even without a quota or expiry. Stars/deleted records are refused by standing cleanup. Audit includes scope hash and task-rules reference. Local owned files are a workflow boundary, not isolation from other same-OS-user processes.

The owner can inspect/revoke an installed grant; revocation prevents the next batch but cannot recall an in-flight provider request. Recovery from uncertain operations requires read-only reconciliation and separate reviewed handling. Never clear uncertainty merely to repeat a write.

When a standing grant is configured, Trash plans go through that grant before ordinary receipt/trusted-client execution. A receipt cannot override its starred-message or source-folder refusal. There is no automatic fallback. An explicitly enabled [managed-policy interface](policy-management.md) provides a separate exact one-time exception workflow; otherwise unsupported scope remains blocked. Do not change flags to circumvent the standing rule.
