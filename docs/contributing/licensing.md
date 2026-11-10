# Licensing and provenance

The project is distributed under the MIT license. Preserve [LICENSE](../../LICENSE), [NOTICE.md](../../NOTICE.md) and the notices for referenced upstream material when redistributing or modifying it.

## Upstream references

The project was developed with reference to [mike-tih/icloud-mcp](https://github.com/mike-tih/icloud-mcp), revision `9769f3b873991ac622932a6337d101dec7e13ced`. Its [pinned MIT license](https://github.com/mike-tih/icloud-mcp/blob/9769f3b873991ac622932a6337d101dec7e13ced/LICENSE) is preserved in root `LICENSE` and `third_party/icloud-mcp/LICENSE`.

The active `icloud_for_agents` package is a separate implementation informed by upstream's iCloud mail and DAV discovery patterns. It does not import or execute the upstream server. The upstream notice covers the referenced material; original additions use the project's declared license.

The optional native Reminders recovery-view implementation also uses [RemCTL](https://github.com/viticci/remctl) as a reference. Its MIT notice is preserved in `third_party/remctl/LICENSE` and recorded in `NOTICE.md`. No RemCTL executable or runtime dependency is bundled.

## Dependency notices

Runtime dependencies are FastMCP, IMAPClient and PyYAML, with resolved transitive dependencies in the platform lock. These retain their own licenses. Build tools and separately installed tunnel clients also have their own terms.

[`docs/reference/dependency-licenses.json`](../reference/dependency-licenses.json) records locked distribution metadata and license-file hashes. Run:

```sh
.venv/bin/python scripts/check-dependency-licenses.py
```

When changing dependencies, review their actual license and notice files, update the lock, and use `--write` to refresh the inventory after review. The check recognizes LICENSE and LICENCE spellings and embedded third-party notices. A new bundled dependency or distribution format needs a review of the notices that must accompany it.

The lock permits both macOS 26 native and portable caio wheels at the pinned version. Their COPYING files differ in line endings. The inventory records each verified exact hash and its wheel provenance under `verified_variants`; verification does not accept arbitrary normalized or changed text.

Automated inventory verification checks the recorded evidence. It is not an independent security certification or legal advice about license compatibility.
