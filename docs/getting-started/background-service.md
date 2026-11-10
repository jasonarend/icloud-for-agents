# ChatGPT tunnel background startup

This guide describes the optional OpenAI private tunnel service used for ChatGPT. It is separate from the [HTTP connection](http.md) available to other compatible clients. A local assistant can perform the technical steps below; you do not need to run Terminal commands.

The macOS tunnel launcher uses an existing Keychain runtime key whose service is `icloud-for-agents-tunnel-runtime` and account is the exact tunnel ID. Selected Mail, Calendar or Contacts services separately use the Apple app-specific password described in [installation](installation.md). Notes and Reminders use macOS permissions instead. Create any needed credentials personally; keep secrets out of chat, arguments, YAML and JSON.

Inspect the nonsecret examples under `deployment/`. Your owner-only `local/settings.json` and `local/tunnel/` profile remain private. The supported profile binds the reviewed stdio command, OpenAI control-plane HTTPS host, an environment key reference and loopback-only health endpoint. The launcher validates those constraints before credential retrieval. It strips the Platform key from the MCP child's environment.

```sh
.venv/bin/python scripts/start-private-tunnel.py --check
.venv/bin/python scripts/start-private-tunnel.py --start-with-keychain
```

The first command is credential-free validation; the second starts the real connection. Keep the foreground process alive until background startup has been deliberately configured. A singleton lock rejects duplicate tunnel runtimes. No automatic mutation retry or persistent secret file is created.

For optional login startup, review `.venv/bin/python scripts/install-login-service.py --help` and the example plist before installing. The installer binds paths to your own checkout and account, uses the existing keychain-backed launcher, and must not race a foreground instance. Stop the foreground process personally before activation. Keychain prompts may need owner interaction; the bridge cannot approve them. Startup depends on login, available storage/network and the Mac being awake. This setup does not change sleep, security or credential settings.

Do not relocate the active checkout, `.venv`, official tunnel binary, settings or tunnel profile while the service references those paths. Closing a ChatGPT tab does not stop local access.

## Status, stop and uninstall

From the original checkout, inspect the reviewed per-user launchd service:

```sh
.venv/bin/python scripts/install-login-service.py --status
```

`loaded` means launchd has the service registered; it does not establish tunnel readiness or Apple authentication. `not_loaded` means it is absent from that login domain. `status_unavailable` is an inspection failure, not proof the service stopped.

To stop the service now while retaining its plist for startup at the next login:

```sh
.venv/bin/python scripts/install-login-service.py --stop
```

To stop it and remove its automatic login startup:

```sh
.venv/bin/python scripts/install-login-service.py --uninstall
```

These commands validate the exact owned plist, target only `gui/YOUR_UID/local.icloud-for-agents.tunnel`, and wait briefly for macOS to finish removing the service from launchd before reporting success. They send one stop request; subsequent checks only inspect its state. A verified absence can report a warning if the stop command itself returned an error. If the service remains loaded or its state cannot be verified within the bounded wait, the command reports that uncertainty and retains the installation. Uninstall removes only `~/Library/LaunchAgents/local.icloud-for-agents.tunnel.plist` after confirmed removal. Altered or symlinked definitions are refused. Shutdown does not require a working tunnel binary, profile or Keychain lookup.

A foreground tunnel is separate. Ask your assistant to stop the exact foreground process it started, or use the controls in the client that owns it. The login-service controls do not kill unrelated foreground processes. Neither stop nor uninstall revokes Apple/OpenAI credentials, deletes the tunnel, removes local settings/plans, or erases existing AI conversations. Review those separately if you want to revoke access or remove retained data. To restart a retained, stopped service, your assistant can use `--activate` after reviewing the existing setup.
