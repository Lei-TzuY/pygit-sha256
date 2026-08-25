# Phase 60 — repository fsck plumbing

Phase 60 adds a native SHA-256 `fsck` implementation that validates repository
storage before traversing connectivity.

Highlights:

- inventory loose and packed objects without trusting `all_shas()`
- verify pack/index structure and SHA-256 checksums
- verify packed object hashes
- validate refs, index entries, and shallow boundaries as graph roots
- validate commit/tree/tag relationships and mode/type contracts
- detect missing objects and graph cycles
- classify reachable, unreachable, and dangling objects
- provide `--connectivity-only`, `--unreachable`, `--no-dangling`, and `--strict`
- export structured `FsckIssue`, `FsckReport`, and `fsck()` APIs

See `FSCK.md` for command and API details.
