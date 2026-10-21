# Restart-safe private startup

The macOS launcher uses two separate existing Keychain items: the Apple app-specific password described in [installation](installation.md), and a tunnel runtime key whose service is `icloud-for-agents-tunnel-runtime` and account is the exact tunnel ID. Create these personally; do not put secrets in chat, arguments, YAML or JSON.

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

These commands validate the exact owned plist, target only `gui/YOUR_UID/local.icloud-for-agents.tunnel`, and confirm its removal from launchd before reporting success. Uninstall removes only `~/Library/LaunchAgents/local.icloud-for-agents.tunnel.plist`; a failed/unconfirmed stop retains the file. Altered or symlinked definitions require owner inspection and are refused. Shutdown does not require a working tunnel binary, profile or Keychain lookup.

A foreground tunnel is separate: stop it in its owning Terminal with Control-C. The login-service controls do not kill unrelated foreground processes. Neither stop nor uninstall revokes Apple/OpenAI credentials, deletes the tunnel, removes local settings/plans, or erases existing AI conversations. Review those separately if you want to revoke access or remove retained data. To restart a retained, stopped service, use `--activate` after reviewing the existing setup.
