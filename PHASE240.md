# Phase 240 — Promisor-aware `rev-list --missing=print-info --count`

Phase 240 completes the count framing for the metadata-only partial-clone
`print-info` path introduced in Phase 237 and composed with boundary traversal in
Phase 239.

Supported forms now include:

```text
pygit rev-list --objects --missing=print-info --count <revisions>
pygit rev-list --objects --boundary --missing=print-info --count <revisions>
```

The existing selection options remain available, including `--skip`,
`--max-count`, `--reverse`, `--topo-order`, `--first-parent`, `--all`, and
`--no-object-names`.

## Count framing

Git documents `--missing=print-info` as the `print` missing mode plus additional
metadata inferred from the containing object. A native SHA-256 partial-clone
fixture was therefore used to establish the executable `--missing=print`
counting baseline while preventing lazy fetches.

The observed framing is:

1. expected missing objects are still printed as `?` records;
2. ordinary present object records are suppressed by `--count`;
3. one final integer is printed;
4. that integer counts present objects only;
5. missing objects are not included in the integer.

For boundary traversal, the final count includes selected commits, boundary
commit records, and present objects from the ordered boundary snapshot closure.
Missing promised objects remain visible as `?` records but do not contribute to
the count.

Phase 240 applies exactly the same framing to `print-info`; the only difference
from `print` is that each missing record carries the already-supported
`path=...` and `type=...` metadata.

## SHA domains

The count path does not weaken pygit's dual-hash invariant:

- present repository objects are real local SHA-256 objects;
- selected and boundary commits are local SHA-256 ids;
- unresolved foreign objects appear only through `?` records containing the
  native SHA-1 transport identity known before materialization;
- no surrogate SHA-256 id is manufactured;
- missing native identities are not counted as local objects.

## Boundary and exclusion semantics

Phase 240 reuses the Phase 236/239 snapshot-root planner unchanged.

For example, with a linear three-commit history and `--max-count=1`, the
boundary form considers the visible tip plus its boundary parent. Their two
present trees contribute to the final count, while their two promised blobs are
printed as `?` records and contribute zero to the number.

Explicit negative revisions remain authoritative. A boundary commit may still
be printed/count as boundary framing, but the explicitly excluded commit's
object closure remains subtracted, including both present trees and unresolved
promises.

## No-fetch property

`--missing=print-info --count` remains metadata-only:

- no single-object promisor fetch;
- no batch promisor fetch;
- no lazy materialization;
- no promisor-state mutation.

## Implementation

The print-info renderer now has one shared helper which:

- always renders unresolved missing records;
- optionally suppresses present-object lines;
- returns the number of present objects encountered;
- optionally suppresses only top-level selected commit inventory entries when
  boundary presentation owns commit framing;
- keeps path-bearing commit objects such as gitlinks as ordinary snapshot
  objects.

This lets boundary and non-boundary count modes share one implementation instead
of maintaining parallel counting logic.

## Tests

`tests/test_phase240.py` covers:

- non-boundary `--count` with a promised blob;
- limit-induced boundary count;
- `--skip + --max-count` boundary selection;
- explicit exclusion closure subtraction;
- `--no-object-names` with missing metadata retained;
- ordinary repository behavior;
- unchanged promisor state;
- zero single-object and batch network fetching.

Phase 240 changes no object format, pack/protocol behavior, refs, index,
worktree, promisor storage format, or fsck semantics.
