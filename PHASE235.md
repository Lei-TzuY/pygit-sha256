# Phase 235 — Promisor-aware `rev-list --objects --boundary`

Phase 235 extends the metadata-only partial-clone object traversal introduced in
Phases 232–234 to Git's revision-boundary presentation:

```text
pygit rev-list --objects --boundary --missing=allow-promisor <revisions>
```

## Behaviour

For revision-range boundaries, pygit now:

- selects commits using the existing `rev-list` traversal;
- computes boundary commits from local commit metadata only;
- prints selected commits and `-<oid>` boundary records in Git-compatible order;
- emits the selected commit tree/object closure after the commit/boundary stream;
- silently omits expected unresolved promisor objects;
- performs no single-object or batch demand fetch;
- keeps every repository-visible identity in the SHA-256 domain.

`--reverse`, `--topo-order`, `--first-parent`, `--no-object-names`, and `--count`
are preserved by the metadata-only path. In particular, native Git treats
`--boundary --count` differently from `--objects-edge --count`: boundary commit
records are included in the one aggregate object count, while object-edge lines
are framed separately from the count.

## SHA-256-native boundary

A filtered foreign tree can know an omitted object's upstream SHA-1 before it
knows that object's local SHA-256. Phase 235 never substitutes that native OID
for a repository-visible object ID. Missing promises remain represented only by
the promisor inventory's `native_oid`; output contains only materialized local
SHA-256 OIDs plus local boundary commit SHA-256 OIDs.

## Deliberate limit

`--boundary` combined with `--skip` or `--max-count` is rejected for the
allow-promisor path in this phase. Native Git can turn the commit immediately
outside such an output limit into a boundary and include that boundary commit's
tree closure in `--objects` traversal. The Phase232 inventory intentionally
walks object closure from selected commits only, so accepting this combination
would silently under-report reachable objects.

A later phase can add a boundary-aware object-root planner and then remove this
restriction without weakening the no-fetch or SHA-256-native invariants.

## Native Git comparison

The implementation was checked against native SHA-256 Git for:

- normal revision-range boundary placement;
- `--reverse` boundary placement;
- `--no-object-names` dash framing;
- aggregate `--boundary --count` behaviour;
- the distinct object-closure semantics of limit-induced boundaries.

## Tests

`tests/test_phase235.py` covers metadata-only partial-clone traversal, promisor
state preservation, reverse ordering, counting, no-object-name framing, and the
explicit rejection of limit-induced boundary closure until that planner exists.
The earlier Phase233 unsupported-mode regression is moved to `--parents`, which
remains outside the inventory-backed subset.
