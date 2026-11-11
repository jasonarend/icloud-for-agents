# Contributing and testing

Contributions to features, fixes, client setup and documentation are welcome. Start with a focused issue or pull request that explains the problem, the resulting behavior and how you checked it. Report security vulnerabilities privately through [SECURITY.md](../../SECURITY.md).

## Set up a development environment

Use a separate checkout and virtual environment so development does not interrupt a working installation. Follow the dependency installation steps in [installation](../getting-started/installation.md). The reviewed lock targets macOS arm64 and CPython 3.14. Synthetic tests do not need an Apple account or a client connection.

The main directories are:

| Location | Contents |
| --- | --- |
| `src/icloud_for_agents/` | Server, service adapters, permissions and recovery logic |
| `src/icloud_for_agents/native/` | macOS helper sources |
| `scripts/` | Installation, connection, build and validation utilities |
| `tests/` | Synthetic fixtures, unit tests, protocol checks and native contracts |
| `deployment/` | Nonsecret configuration templates |
| `docs/` | User, setup-agent and contributor guides |

Read [project metadata](metadata.md) when changing server identity or protocol metadata, [branding](branding.md) when changing icons, and [licensing](licensing.md) when adding or redistributing dependencies.

## Choose the right checks

From the project root, start with the offline suite and the real stdio protocol checks:

```sh
.venv/bin/python tests/run_offline.py
.venv/bin/python tests/stdio_smoke.py
.venv/bin/python tests/modern_stdio_smoke.py
.venv/bin/python scripts/check-release.py --local-commit
```

The offline runner isolates its home directory and environment and rejects network, DNS, listeners and subprocesses. The stdio runners start guarded child servers to check initialization, tool discovery, schemas, metadata, errors and read-only enforcement. Reports are written under ignored `build/reports/`.

Run additional checks for the area you change. The complete automated sequence is maintained in [the source-check workflow](../../.github/workflows/checks.yml), rather than duplicated here.

| Area | Relevant checks |
| --- | --- |
| HTTP transport and client permissions | `http_transport_smoke.py`, `http_services_smoke.py`, `http_once_smoke.py`, `http_pairing_stdio_smoke.py`, `http_client_lifecycle_smoke.py` |
| Connection startup | `http_service_process_smoke.py`, `http_service_launchd_smoke.py`, `cloudflare_tunnel_process_smoke.py`, `login_service_launchd_smoke.py` |
| Shared operations and recovery | `operation_lock_process_smoke.py`, `mail_operation_process_smoke.py`, `dual_transport_smoke.py` |
| Calendar recurrence | `calendar_*_stdio_smoke.py` and related unit tests |
| Notes and Reminders | Relevant `native_*_stdio_smoke.py`, native contract runners and unit tests |
| Owner review and standing permissions | `owner_review_stdio_smoke.py`, `policy_stdio_smoke.py`, `native_grant_stdio_smoke.py` |
| Dependencies and packages | `check-dependency-licenses.py`, `check-sdist.py`, `check-branding-package.py` under `scripts/` |

Run test files with `.venv/bin/python tests/FILE.py` and scripts with `.venv/bin/python scripts/FILE.py`. Some native contract runners require Apple developer tools. Use the SDK associated with the selected developer installation for that test process, as shown in the workflow; avoid changing the Mac's system-wide developer-tool selection to fix a test environment.

The HTTP tests use synthetic credentials and loopback listeners. The native HTTP Keychain check uses a disposable standalone Keychain, verifies that the existing default and search list are unchanged, and removes its temporary Keychain. The launchd checks temporarily register isolated jobs with harmless dummy processes, then remove them. Review these runners before executing them on a shared machine. Native contract tests use synthetic application objects or in-memory Foundation/EventKit values; they do not establish that a feature works in the live Apple apps.

## Changes to permissions and data operations

Preserve account and item identity checks, ETags, snapshot comparisons, operation locks, one-time authorization markers and recovery behavior. Add regression coverage for failure and uncertain outcomes as well as success. A timed-out write may still have taken effect; an automatic retry must not duplicate it.

Tool annotations describe behavior to a client. They do not authorize an operation. Keep server-side access controls and owner review effective through every transport. New transports must explain authentication, secret handling, startup and shutdown, and how existing approval and account checks remain enforced.

Use invented data for automated tests. Live verification is optional and requires the account owner's permission for the exact access and changes involved. Keep credentials, account content, private settings, real object identifiers and local execution records out of commits and reports.

## Client and platform compatibility contributions

Help improve the [client setup guide](../getting-started/clients.md) by reporting a working setup or a reproducible problem. Include:

- Client and project versions, macOS version, architecture and Python version.
- Where the MCP server runs, its transport and sanitized setup steps.
- What succeeded: discovery, service reads, approval flows or changes. Describe any untested steps separately.
- Relevant official client documentation and sanitized errors.

For hosted clients, use the shared [HTTP connection](../getting-started/http.md) and HTTPS routing guidance when the client supports it. Client keys, Cloudflare Tunnel and tailnet routing are connection options for compatible clients, including custom agents. The OpenAI private tunnel is specific to ChatGPT.

Other architectures and Python versions need their own dependency and runtime validation. A port to another operating system also needs a secure credential provider and alternatives to the native macOS integrations. Package installation alone does not establish service support.

## Prepare a pull request

Keep changes focused, update the affected user or agent guide, and summarize the checks you ran and any practical limits. Do not include generated caches, private fixtures or personal setup records. Before a distribution change, inspect the source archive with `scripts/check-sdist.py` and the wheel with `scripts/check-branding-package.py` using the project interpreter. Confirm that the archive can run the relevant checks from an extracted copy.

Version and release preparation are described in [releasing](releasing.md). Ordinary documentation changes do not need a changelog entry.
