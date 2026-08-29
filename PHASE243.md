# Phase 243 — promisor-aware object-edge + boundary framing

Phase 243 completes the metadata-only `rev-list` missing-object composition for
`--objects-edge --boundary`.

## Why this phase exists

Git allows `--objects-edge` and `--boundary` together. Their output semantics
are not a naive concatenation: a commit that is both an explicit exclusion edge
and a boundary is emitted once as a leading `-<oid>` object-edge record. Under
`--count`, that edge is advertised but is not part of the final present-object
count.

Phase 242 deliberately rejected this combination until that interaction was
modelled. Phase 243 lifts the restriction for both:

```text
rev-list --objects-edge --boundary --missing=print ...
rev-list --objects-edge --boundary --missing=print-info ...
```

## Implementation

The implementation keeps the existing metadata-only traversal authoritative:

1. project `--objects-edge` to the already-tested `--objects` missing traversal;
2. obtain excluded edge commits through the Phase 234 metadata-only edge walker;
3. obtain boundary framing through the Phase 235 boundary planner;
4. identify edge/boundary overlap by local commit SHA-256;
5. emit object edges first and remove only duplicate boundary presentation;
6. for `--count`, subtract the duplicate edge/boundary commits from the projected
   present-object count instead of altering missing-object reporting.

Distinct limit-induced boundaries are not removed merely because boundary mode
is enabled; only commits proven to be both an object edge and a boundary are
deduplicated.

## SHA-256-native boundary

All present objects, object edges, and boundary commits remain real local
64-hex SHA-256 object IDs. Unmaterialized foreign promises remain confined to
the explicit `?` missing-object channel and retain their known native SHA-1
transport identity. No padding, surrogate SHA-256, or hash-domain alias is
introduced.

## Partial-clone behavior

The phase performs no single-object or batch promisor fetch. It reuses local
commit/tree metadata, leaves promisor state unchanged, and continues to exclude
the negative revision's tree/blob closure from selected object inventory.

## Compatibility verification

Native SHA-256 Git was checked with explicit revision ranges. The relevant
observable rules are:

- `--objects --boundary` can show an excluded boundary in the commit stream;
- adding `--objects-edge` moves an explicit exclusion edge to a leading
  `-<oid>` record instead of duplicating it as a later boundary record;
- `--reverse` reverses selected commit ordering but the object edge remains a
  leading framing record;
- `--objects-edge --boundary --count` emits the edge record and a count which
  excludes that edge.

The focused tests cover plain `print`, `print-info`, `--count`, no-overlap
boundary mode, zero-fetch behavior, SHA-domain separation, and unchanged
promisor state. Full Python 3.9 and 3.13 GitHub Actions remain the final gate.
