# Distribution and release verification

## Per-user source and private connections

Source distribution lets each user install their own environment and connect their own Apple credentials and MCP client. It must never contain a working user's Keychain material, settings, tunnel profile, message data, approval records, handoffs or supplied private assets. Local runtime paths are ignored and are not release inputs.

ChatGPT private custom MCP connections are distinct from public plugins. Catalog eligibility requires a separate provider review. OpenAI's [desktop-only deployment guide](https://developers.openai.com/plugins/deploy/desktop-only) describes local stdio packaging and current partner restrictions; [plugin building](https://developers.openai.com/plugins/build/plugins) and [third-party integration guidelines](https://developers.openai.com/plugins/plugin-guidelines#third-party-content-and-integrations) add eligibility requirements. Desktop-specific packaging is not included. Do not promise a public catalog listing or compatibility with untested clients.

## Before a release

- Review the exact staged/source archive and wheel/sdist contents, Git history and dependency licenses. Ignore rules alone do not remove tracked data.
- Run guarded tests and credential-free discovery from a sanitized copy; build/install the package into a temporary environment with no private settings.
- Distinguish reused installed dependencies from a fresh network dependency bootstrap and a clean OS test. Record versions, platform and limits.
- Validate relative documentation links and all install paths after a move. Keep source/package version separate from app wrapper and tunnel-client versions.
- Build the sdist, run `.venv/bin/python scripts/check-sdist.py dist/icloud_for_agents-VERSION.tar.gz`, then extract it into a disposable directory and run the documented offline/stdio checks there. The archive must contain every test runner and fixture, not just `test_*.py`.
- Before the first public release, enable GitHub private vulnerability reporting in the repository's security settings and verify the **Report a vulnerability** form linked from [SECURITY.md](../../SECURITY.md). Do not describe this channel as active until the repository exists and the setting has been checked. This local checkout cannot activate the GitHub setting.
- Review the complete relevant security surface. A partial scan or passing privacy patterns is not an exhaustive assurance.
- Obtain explicit approval before a remote, commit, push, package upload or publication.
