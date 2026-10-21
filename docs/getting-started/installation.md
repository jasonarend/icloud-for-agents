# Installation and credentials

## Prerequisites

The verified environment is macOS arm64, CPython 3.14. Python 3.11+ is declared in package metadata; other combinations require separate validation. The hash lock is platform/interpreter-specific. There is no Windows/Linux installer. Install only the root package, not an unrelated upstream server.

From the reviewed project root:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-macos-arm64-py314.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m pip check
```

Installation downloads dependencies. A different platform needs its own reviewed resolution and hashes. Do not disable hash checks merely to make this lock fit.

## Apple credential

Create an Apple app-specific password yourself through Apple's account security interface. In macOS Keychain Access create a generic password item:

| Field | Value |
| --- | --- |
| Item name/service | `icloud-for-agents` |
| Account | Your exact iCloud login address |
| Password | Your app-specific password, entered personally |

Never use your main Apple Account password. Do not paste credentials into chat, terminal commands, screenshots, source, `.env` or JSON files. Handle Keychain access/unlock prompts personally. The bridge reads the existing item when required; it never provisions it. A trusted launcher can alternatively supply a bounded secret through an inherited descriptor; see [security](../security/model.md) for that trust boundary.

## Nonsecret configuration

For direct local MCP use, no OpenAI account, tunnel or client name is required. After reviewing local installation and account access, create a new nonsecret profile:

```sh
.venv/bin/python scripts/configure-local.py --account YOUR_ICLOUD_LOGIN
```

This creates owner-only `local/settings.json`, defaults to read-only Mail, and leaves Calendar/Contacts disabled. To select a different approved subset, add `--services mail,calendar`, `--services contacts`, or `--services none`. Every listed name is enabled and every omitted service disabled. The legacy `--enable-dav-reads` option enables both DAV services and cannot be combined with `--services`. Writes require separate scope approval before using `--enable-writes`; it does not enable trusted-client mode or create a standing grant.

Show generic client registration information for existing settings without changing them:

```sh
.venv/bin/python scripts/configure-local.py --show-stdio-config
```

It returns an absolute Python `command` and the `scripts/run-stdio.py` argument. Follow the [client-specific setup guide](clients.md) for registration and verification. Use the chosen client's actual supported registration format; there is no universal UI or config filename. An authorized setup agent should inspect known existing supported configuration when possible, avoid dumping secrets, and ask only if multiple plausible targets or missing access make the choice ambiguous. Do not install additional live clients or modify unknown configuration to guess the answer.

The client-neutral wrapper validates settings and strips unrelated inherited variables. Direct launch of `.venv/bin/icloud-for-agents` remains supported with nonsecret `ICLOUD_MAIL_ACCOUNT`, optional `ICLOUD_KEYCHAIN_SERVICE`, `ICLOUD_MAIL_ACCESS=read-only`, and independent `ICLOUD_ENABLE_MAIL`, `ICLOUD_ENABLE_CALENDAR`, `ICLOUD_ENABLE_CONTACTS` values (`0` or `1`). Legacy `ICLOUD_ENABLE_DAV_READ=1` remains a fallback for both DAV services when their explicit switches are absent. It does not itself read `local/settings.json`. No password goes in arguments or environment variables.

## Optional ChatGPT tunnel

For a new approved OpenAI tunnel, the same utility accepts optional `--tunnel-id YOUR_TUNNEL_ID`. Only this route creates a tunnel profile and requires a separate Platform runtime credential. Existing profiles are never overwritten. See [ChatGPT setup](chatgpt.md). Do not create a tunnel for a local client that already supports stdio.

The [ChatGPT executable installation steps](chatgpt.md#install-and-select-the-tunnel-executable) document the reviewed client version and optional absolute `tunnel_client_path` setting. The launcher does not discover an executable from PATH or download it automatically.

Verify actual read-only folder/message access first, then calendar/address-book reads if enabled. Schema discovery is not account authentication. Claude Desktop/Code and other stdio clients are compatible in principle, but only ChatGPT/private tunnel and the synthetic stdio harness have been tested with this project. HTTP/SSE-only clients are unsupported; the bridge implements stdio, not an HTTP MCP server.
