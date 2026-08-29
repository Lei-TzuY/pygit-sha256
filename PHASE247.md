# Phase247: metadata-only `rev-list --filter=blob:none --count`

Phase247 extends the Phase246 metadata-only `blob:none` adapter to Git-style
object counting without materializing promised objects.

## Problem

Phase246 can filter line-oriented promisor-aware `rev-list --objects` output,
but deliberately rejected `--count`. Simply forwarding `--count` to the
underlying unfiltered traversal would be wrong for repositories that already
have local blobs: the numeric result would still include blobs even though
`--filter=blob:none` removes them from the advertised object set.

The count path also has two independent presentation channels which must not be
confused with selected present objects:

- `--objects-edge` keeps explicit excluded commits visible as `-<oid>` but does
  not count them.
- `--missing=print` / `print-info` keeps `?` records visible but does not count
  missing objects.

Boundary commits are different: a genuine `--boundary` commit is a present
traversal object and does contribute to the count. In particular, under
`--reverse` a limit-induced boundary may be the first textual `-<oid>` record,
so classifying every leading dash record as an object edge would be incorrect.

## Native Git baseline

A native SHA-256 repository was exercised directly. The observed contract is:

- `rev-list --objects --filter=blob:none --count HEAD` reports the number of
  present records in the *filtered* object stream.
- local blobs omitted by `blob:none` do not contribute to the integer.
- `--objects-edge --count` still prints explicit `-edge` records, but those
  excluded commits do not contribute to the integer.
- `--boundary --count` counts boundary commits as present objects.
- with `--objects-edge --boundary --max-count`, an explicit exclusion edge is
  still excluded from the count while a distinct limit-induced boundary is
  counted.

## Implementation

Phase247 does not change `promisor_object_inventory` or revision selection.
Instead, count mode deliberately reuses the established uncounted traversal:

1. remove only `--filter=blob:none` and `--count` from the projection;
2. run the existing Phase237-243 metadata-only missing/object-edge/boundary
   presentation;
3. remove present blobs by reading only already-local SHA-256 objects;
4. remove promised blobs through persistent promisor kind metadata without
   fetching them;
5. retain non-blob `?missing` records but do not count them;
6. retain explicit object-edge records but do not count them;
7. count every remaining present record, including genuine boundary commits;
8. emit the final integer.

Explicit object edges are identified by reusing Phase234's
`_promisor_object_edges()` planner. This intentionally avoids a textual
"leading dash means edge" heuristic, which would misclassify a reverse-ordered
boundary when there is no explicit exclusion edge.

## Supported composition

The new count behavior composes with the Phase246 line-oriented filter path:

- `--objects`
- `--objects-edge` where the underlying missing mode supports it
- `--boundary`
- `--skip` / `--max-count`
- `--reverse`
- `--missing=allow-promisor`
- `--missing=print`
- `--missing=print-info`

The Phase246 `-z + --filter=blob:none` combination remains deliberately
unsupported. NUL metadata has its own record protocol and should be modeled in
a separate phase rather than reconstructed from line-oriented output.

## SHA identity boundary

Phase247 preserves the existing dual-domain rule:

- selected present objects, object edges, and boundary commits use genuine
  repository-visible 64-hex SHA-256 identities;
- unresolved foreign identities may appear only on the explicit `?missing`
  channel;
- a promised blob filtered by `blob:none` is neither printed nor counted;
- no surrogate SHA-256 identity is invented.

## Network and mutation guarantees

The count path remains metadata-only:

- no single-object promisor fetch;
- no batch promisor fetch;
- no checkout or worktree mutation;
- no index/ref mutation;
- no promisor-state mutation.

## Regression coverage

Focused tests cover:

- an ordinary repository where a present local blob must be removed from the
  numeric count;
- `allow-promisor`, plain `print`, and `print-info` over a real foreign
  `blob:none` promise with zero fetches and unchanged promisor state;
- `--objects-edge --boundary --max-count --count`, proving explicit edges are
  excluded while a distinct limit-induced boundary is counted;
- `--reverse` with `--objects-edge` but no explicit exclusion edge, proving a
  front-positioned boundary is still counted;
- continued rejection of `-z + --filter=blob:none`.

Phase247 changes no object format, tree serialization, pack format, wire
protocol, ref/index/worktree format, or promisor identity representation.
