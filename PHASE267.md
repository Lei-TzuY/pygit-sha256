# Phase267: ordered `blob:limit` filtering on the current stack

Phase267 rebases the earlier Phase258 `rev-list --filter=blob:limit=<n>[kmg]`
work onto the current Phase266 ordered-object stack and composes it with
`--in-commit-order`.

The earlier Phase258 / PR #235 is a sibling of the Phase259+ ordered line and is
not an ancestor of Phase266. Phase267 therefore cleanly ports that mature
size-filter implementation into the current stack instead of rewriting the old
branch or pretending that its commits are already present.

## Supported behavior

Phase267 supports the current-stack non-ordered line/count behavior from
Phase258 and adds ordered line/count composition for:

```text
pygit rev-list --objects --in-commit-order --filter=blob:limit=<n>[kmg] <revs...>
pygit rev-list --objects-edge --in-commit-order --filter=blob:limit=<n>[kmg] <revs...>
```

The ordered path composes with:

- `--reverse`
- `--skip`
- `--max-count`
- `--topo-order`
- `--first-parent`
- `--all`
- `--boundary`
- `--objects-edge`
- `--count`
- `--no-object-names`
- `--missing=allow-promisor|print|print-info` for objects whose membership is
  classifiable without fetching
- `--filter-provided-objects`; in the current commit-rooted ordered model this
  has no visible effect because `blob:limit` filters blobs while provided roots
  are commits

Phase267 deliberately keeps these combinations deferred:

- ordered `blob:limit` + `-z`
- ordered `blob:limit` + `--filter-print-omitted`
- ordered `blob:limit` + `--disk-usage`

The first two have distinct framing/omission semantics and should be added in
focused follow-up phases instead of being guessed here.

## Git compatibility

Git 2.55 documents `blob:limit=<n>[kmg]` as omitting blobs whose size is **at
least** the requested threshold. A threshold of zero therefore removes every
blob. The suffixes are binary units: `k == 1024`, `m == 1024^2`, and
`g == 1024^3`.

Git also documents `--in-commit-order` as emitting tree/blob object IDs after
the first commit that references them. Phase267 preserves that order by applying
size membership directly to the already-structured ordered inventory; it never
reconstructs order from rendered lines.

Upstream Git's `filter_blobs_limit()` obtains the object's size, keeps blobs
smaller than the threshold, and hard-omits blobs whose size is greater than or
equal to it. When omission collection is enabled upstream, those filtered blobs
are added to the omitted OID set. Phase267 intentionally defers that omitted
presentation channel even though the membership rule itself is implemented now.

## Partial clone and SHA domains

Present objects remain genuine local SHA-256 objects. An unresolved promised
blob has a native/upstream identity and type, but pygit's current persistent
promisor metadata does **not** store the blob's uncompressed size.

Phase267 therefore refuses to classify an unresolved promised blob rather than:

- demand-fetching it merely to apply `blob:limit`,
- guessing its size,
- padding or translating its native SHA-1,
- or exposing that transport identity as a repository-visible SHA-256.

The refusal happens before rendering ordered traversal output. Tests prohibit
both intentional single-object and batch materialization and verify that
`.pygit/promisor.json` remains unchanged.

This is stricter than upstream Git's internal fallback for an unavailable local
blob, which conservatively shows an object it cannot size. The stricter pygit
behavior preserves the project's metadata-only filter contract until persistent
size metadata exists.

## Architecture

`pygit/rev_list_filter_blob_limit_cli.py` is the clean current-stack port of the
Phase258 adapter and remains the shared source for:

- filter parsing and binary unit conversion,
- local blob-size lookup,
- unresolved-promisor size preflight,
- non-ordered line/count compatibility.

`pygit/rev_list_in_commit_order_blob_limit_cli.py` owns only the ordered
composition. It:

1. parses `blob:limit` through the shared adapter,
2. asks the Phase259-266 ordered walker for structured inventory,
3. preserves object-edge/boundary deduplication,
4. validates that all reachable promised blobs are classifiable without fetch,
5. filters local blobs by size without changing entry order,
6. delegates line/count rendering back to the shared ordered renderer.

No second object walker and no second hash-domain model is introduced.

## Regression coverage

Phase267 tests cover:

- clean current-stack port of the original non-ordered threshold behavior,
- non-ordered filtered count,
- exact ordered commit/snapshot first-seen positions,
- reverse traversal,
- boundary snapshot ordering,
- object-edge framing and excluded-closure subtraction,
- filtered count,
- threshold zero,
- KiB suffix semantics,
- acceptance of `--filter-provided-objects` for commit-rooted traversal,
- unresolved promised-blob refusal before output,
- zero intentional single/batch fetches,
- unchanged promisor state,
- invalid filter syntax,
- explicit deferral of ordered NUL, omitted-object, and disk-usage composition.

## Follow-up

The most direct next steps are:

1. ordered `blob:limit + --filter-print-omitted`, using genuine local SHA-256
   omitted identities and upstream's omission-set semantics;
2. ordered `blob:limit + -z`, preserving the current Git mixed
   NUL-traversal/newline-omission framing when both are eventually composed;
3. persistent promised-object size metadata, which would allow size filters to
   classify unresolved blobs without materialization.
