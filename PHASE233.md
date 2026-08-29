# Phase 233 — promisor-aware `rev-list --missing=allow-promisor`

Phase 232 introduced a metadata-only object inventory that preserves pygit's
repository-visible SHA-256 object domain while representing unresolved foreign
promises separately by their native transport OID. Phase 233 wires that
inventory into the first safe `rev-list --missing` presentation mode.

## Behaviour

`pygit rev-list --objects --missing=allow-promisor <revisions>` now traverses
local commit/tree metadata without materializing promised objects. Present
objects are printed with their real local SHA-256 object IDs. Expected missing
promisor objects are silently omitted, matching native Git's
`--missing=allow-promisor` behaviour.

This is deliberately different from `--missing=print`: an unresolved foreign
blob has only its upstream/native SHA-1 until content arrives, so pygit must not
print that SHA-1 as though it were a repository SHA-256 object ID. `print` and
`print-info` therefore remain deferred until the output hash-domain contract is
explicitly designed.

The adapter currently supports the inventory-backed selection controls
`--all`, `--first-parent`, `--topo-order`, `--reverse`, `--skip`, `--max-count`,
`--count`, and `--no-object-names`. Object-edge, boundary, relation, age,
parent-count, header/timestamp, and disk-usage combinations are rejected rather
than silently falling back to a traversal that could fault promised objects in.

## SHA-256-native invariant

- local commit/tree/blob objects are exposed only by their 64-hex SHA-256 OID;
- unresolved promised objects retain native SHA-1 only inside promisor metadata;
- `allow-promisor` omits those promises and performs no network fetch;
- no surrogate SHA-256 identity is synthesized.

## Verification

Focused tests use a real foreign `blob:none` import and assert that both single
and batch fetch seams remain untouched, promisor state is unchanged, unresolved
native SHA-1 values never appear in output, `--no-object-names` remains
machine-parseable, ordinary repositories stay entirely SHA-256-native, and
unsupported object modes fail explicitly.

The full project test suite is expected to run on Python 3.9 and 3.13 in GitHub
Actions before this phase is considered complete.
