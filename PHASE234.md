# Phase 234 — promisor-aware `rev-list --objects-edge`

Phase 233 added SHA-256-safe `rev-list --objects --missing=allow-promisor`.
Phase 234 extends the same metadata-only path to `--objects-edge` so partial
clone plumbing can expose thin-pack boundary commits without faulting promised
blob content into the local store.

## Behaviour

`pygit rev-list --objects-edge --missing=allow-promisor <revisions>` now:

- computes excluded boundary commits entirely from local commit metadata;
- emits each boundary commit as `-<sha256>` before the selected object output;
- keeps promised missing blobs silently omitted, as required by
  `--missing=allow-promisor`;
- leaves all repository-visible object identities in the local SHA-256 domain;
- performs no single-object or batch promisor fetch;
- preserves `--no-object-names`, `--first-parent`, ordering controls, `--skip`,
  `--max-count`, and `--count` through the Phase 233 adapter.

Boundary discovery deliberately ignores output limits such as `--max-count`.
Native Git still emits the revision exclusion edge even when the selected commit
listing is limited before the edge-adjacent commit itself would be printed.
Similarly, native `--objects-edge --count` prints edge lines separately and does
not include them in the following object count; pygit now matches that framing.

`--boundary` remains deferred because its placement is part of the commit-list
presentation rather than the object-edge prelude and should be implemented as a
separate compatibility phase instead of being conflated with thin-pack edges.

## Git compatibility

Git documents `--objects-edge` as `--objects` plus excluded commits prefixed
with `-`, primarily for thin-pack construction. Native Git comparisons for this
phase confirm:

- excluded edge commits are printed before ordinary object output;
- `--no-object-names` does not alter the `-<oid>` edge representation;
- `--max-count` limits ordinary selected output but does not erase the revision
  exclusion edge;
- `--count` prints the edge line(s) followed by a count that excludes them.

## SHA-256-native invariant

The edge commits are already-local commit objects, so they are emitted only by
their real 64-hex SHA-256 repository OIDs. Unresolved foreign blobs retain their
native SHA-1 only inside promisor metadata and are omitted by allow-promisor.
No surrogate SHA-256 identity is synthesized.

## Verification

Focused tests cover a real foreign `blob:none` two-commit history, zero network
fetches, unchanged promisor state, SHA-256-only edge/output identities, native
`--count` framing, `--max-count` boundary independence, and rejection of an
ambiguous simultaneous `--objects` + `--objects-edge` request.

The full project test suite is required to pass on Python 3.9 and 3.13 in GitHub
Actions before this phase is considered complete.
