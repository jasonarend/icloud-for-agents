# Licensing and provenance

## Upstream reference

The project was developed with reference to [mike-tih/icloud-mcp](https://github.com/mike-tih/icloud-mcp), revision `9769f3b873991ac622932a6337d101dec7e13ced`. Its [pinned MIT license](https://github.com/mike-tih/icloud-mcp/blob/9769f3b873991ac622932a6337d101dec7e13ced/LICENSE) is preserved unchanged in root `LICENSE` and `third_party/icloud-mcp/LICENSE`; provenance is recorded alongside it.

The active `icloud_for_agents` package is a separate implementation informed by upstream's iCloud mail and DAV discovery patterns. It does not import or execute the upstream server. This attribution does not claim every new safety, approval, pagination, launcher or test module was written by upstream. The MIT notice is retained for upstream material; original additions in this repository use the declared project license.

## Dependencies

Runtime dependencies are FastMCP, IMAPClient and PyYAML, with resolved transitives in the platform lock. They are installed dependencies, not copied source under this project's MIT license. The IMAPClient adapter subclasses its public implementation and does not vendor that package. `docs/reference/dependency-licenses.json` records installed locked-distribution metadata and license-file hashes from the reviewed environment; absence of machine-readable metadata is explicitly unresolved. Consult each distribution's actual notices before redistributing bundled dependencies. Build tooling and the separately installed official tunnel client are not covered by the application MIT notice.
