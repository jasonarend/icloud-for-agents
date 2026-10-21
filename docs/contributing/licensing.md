# Licensing and provenance

## Upstream reference

The project was developed with reference to [mike-tih/icloud-mcp](https://github.com/mike-tih/icloud-mcp), revision `9769f3b873991ac622932a6337d101dec7e13ced`. Its [pinned MIT license](https://github.com/mike-tih/icloud-mcp/blob/9769f3b873991ac622932a6337d101dec7e13ced/LICENSE) is preserved unchanged in root `LICENSE` and `third_party/icloud-mcp/LICENSE`; provenance is recorded alongside it.

The active `icloud_for_agents` package is a separate implementation informed by upstream's iCloud mail and DAV discovery patterns. It does not import or execute the upstream server. This attribution does not claim every new safety, approval, pagination, launcher or test module was written by upstream. The MIT notice is retained for upstream material; original additions in this repository use the declared project license.

## Dependencies

Runtime dependencies are FastMCP, IMAPClient and PyYAML, with resolved transitives in the platform lock. They are installed dependencies, not copied source under this project's MIT license. The IMAPClient adapter subclasses its public implementation and does not vendor that package. `docs/reference/dependency-licenses.json` records installed locked-distribution metadata and license-file hashes from the reviewed environment; absence of machine-readable metadata is explicitly unresolved. Consult each distribution's actual notices before redistributing bundled dependencies. Build tooling and the separately installed official tunnel client are not covered by the application MIT notice.

Run `.venv/bin/python scripts/check-dependency-licenses.py` to check all locked distribution versions and inventoried notice paths/hashes against the installed environment. After deliberately reviewing dependency changes, `--write` refreshes that inventory. The collector recognizes both LICENSE and LICENCE spellings, including aiofile's two Apache-2.0 notice files, and embedded third-party NOTICE files. It excludes Python modules merely named `license.py`.

## Evidence and certification

Passing tests, advisory checks and notice inventory verification are release evidence, not an independent security certification or a legal opinion on license compatibility. The project claims neither. MIT attribution and dependency notice evidence must stay intact; a new bundled dependency, transport or distribution arrangement requires its own review. If an external certification or legal sign-off is needed, identify its scope and qualified independent reviewer before describing a release as certified. A missing inventory entry alone does not establish a license violation.
