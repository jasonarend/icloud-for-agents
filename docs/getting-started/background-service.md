# Restart-safe private startup

The macOS launcher uses two separate existing Keychain items: the Apple app-specific password described in [installation](installation.md), and a tunnel runtime key whose service is `icloud-for-agents-tunnel-runtime` and account is the exact tunnel ID. Create these personally; do not put secrets in chat, arguments, YAML or JSON.

Inspect the nonsecret examples under `deployment/`. Your owner-only `local/settings.json` and `local/tunnel/` profile remain private. The supported profile binds the reviewed stdio command, OpenAI control-plane HTTPS host, an environment key reference and loopback-only health endpoint. The launcher validates those constraints before credential retrieval. It strips the Platform key from the MCP child's environment.

```sh
.venv/bin/python scripts/start-private-tunnel.py --check
.venv/bin/python scripts/start-private-tunnel.py --start-with-keychain
```

The first command is credential-free validation; the second starts the real connection. Keep the foreground process alive until background startup has been deliberately configured. A singleton lock rejects duplicate tunnel runtimes. No automatic mutation retry or persistent secret file is created.

For optional login startup, review `.venv/bin/python scripts/install-login-service.py --help` and the example plist before installing. The installer binds paths to your own checkout and account, uses the existing keychain-backed launcher, and must not race a foreground instance. Stop the foreground process personally before activation. Keychain prompts may need owner interaction; the bridge cannot approve them. Startup depends on login, available storage/network and the Mac being awake. This setup does not change sleep, security or credential settings.

Do not relocate the active checkout, `.venv`, official tunnel binary, settings or tunnel profile while the service references those paths. To stop the service use the installed service controls described by the installer; do not infer that closing a ChatGPT tab stops local access.
