# Upgrade, rollback and uninstall

**Before an upgrade**

Record the working source revision, Python/package versions, dependency lock, tunnel executable version/digest and client configuration. Identify the original checkout, virtual environment, login plist, nonsecret settings and effective runtime directory. Runtime defaults to `~/Library/Application Support/icloud-for-agents/runtime`; an explicit `runtime_directory` setting overrides that default.

Reconcile every unfinished or uncertain action before changing versions. Stop foreground access and use the [login-service stop controls](background-service.md#status-stop-and-uninstall) where applicable. Closing the AI client does not necessarily stop its server or tunnel. Keep credentials in Keychain; backups of settings, plans and receipts contain private account/message data and need owner-only permissions and appropriately protected storage.

Test the candidate source and locked dependencies in a separate disposable checkout/environment first. Run offline tests, both stdio suites, dependency-notice verification and archive checks. Preserve the working checkout/environment for rollback, including their original absolute paths. Do not copy an active virtual environment to a different location and assume its interpreter scripts will work there.

**Switching versions**

Switch only while the runtime is stopped. Review configuration/schema changes explicitly. A separate checkout needs its own validated settings and tunnel profile with its actual absolute paths; preserve the existing credential service/account labels and approved service subset. If it uses existing runtime records, explicitly select the same private runtime directory. Do not silently create a second instance or a different audit history.

Update the intended client's exact command/args and, if used, replace the reviewed login plist through the uninstall/install workflow. Verify read-only access through that client before separately restoring approved write/cleanup settings. Keep the old environment until the new connection is verified. A successful offline test does not establish live account access.

**Rollback**

Stop the candidate runtime. Restore the prior source, dependency environment and configuration at their original paths, then restore the reviewed client/login registration. Verify read-only access again. Never roll back, overwrite or delete runtime journals, grants, receipts or policy state to clear a failed action. Restoring old code does not undo mail, event or contact changes. If the prior version cannot interpret current state, keep access stopped and reconcile/review the compatibility gap instead of forcing startup.

**Uninstall and retained data checklist**

- Stop each foreground server/tunnel, uninstall the reviewed login service, and remove the intended client registration. Confirm no separate client or foreground instance remains active.
- If ending Apple access, revoke the exact app-specific password in Apple Account settings. Removing a local Keychain item alone does not revoke the credential with Apple. Shared credentials may affect other explicitly configured installations.
- If ending ChatGPT tunnel access, disconnect the client connection and revoke/delete the corresponding Platform runtime key/tunnel through the owner's account. These are separate from stopping the local process.
- Decide whether to retain or remove the exact Apple Keychain item (`icloud-for-agents`, account = mailbox) and tunnel Keychain item (`icloud-for-agents-tunnel-runtime`, account = tunnel ID), including any explicitly configured service-name overrides. Handle native prompts personally.
- Review `local/settings.json`, `local/tunnel/` profiles, health/lock files and any logs. They may contain account identifiers and private connection metadata. Remove only the identified installation's files after access is stopped.
- Review the effective runtime directory: proposals may contain full outgoing message text, recipients and DAV before-snapshots; receipts/results, cleanup-authority, cleanup-audit and managed-policy state are also retained. Complete reconciliation before deliberate removal. Never blanket-delete state to retry an uncertain write.
- Remove the checkout, virtual environment, downloaded tunnel binary and private backups only after deciding which records must be retained. Check any custom log/output paths and separately created copies.
- Review AI conversation history, client logs/backups and external provider retention controls separately. Local uninstall cannot erase copies already held by Apple, an AI provider or a backup service.

The scripts do not automate credential revocation or private-record deletion. Service uninstall removes only its reviewed login plist. This checklist describes the repository's known local data locations; external applications may retain additional records.
