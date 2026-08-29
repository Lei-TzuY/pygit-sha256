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
traversal object and does contribute to the count. Phase240/243 already model
that distinction, including edge/boundary overlap and limit-induced boundary
snapshot roots, so the filter layer must not reconstruct those semantics from
textual `-` records.

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
Count mode keeps the existing Phase240/243 count implementation authoritative:

1. remove only `--filter=blob:none` and run the existing projected request **with
   `--count` still present**;
2. retain that output's already-correct edge/boundary/missing framing and final
   unfiltered present-object integer;
3. run a second metadata-only inspection traversal with `--count` removed;
4. for inspection only, project `--objects-edge` to `--objects` so Phase236's
   boundary snapshot-root planner exposes the complete selected/boundary object
   closure while the original negative revisions remain authoritative;
5. count only already-present local `BlobObject` records in that inspection;
6. subtract that present-blob count from the authoritative numeric result;
7. remove promised blob `?missing` records from the retained count framing;
8. leave explicit object-edge records and non-blob missing records unchanged.

This split is intentional. The line-oriented uncounted Phase243 presentation is
not itself a sufficient source for reconstructing count semantics in every
edge/boundary combination; the established count path may compute boundary
snapshot contributions directly from inventory. Phase247 therefore performs
filter subtraction instead of reimplementing that logic.

Promised blobs require no numeric subtraction because existing missing-object
count modes never count them in the first place. Persistent promisor kind
metadata is used only to suppress their `?missing` records, without fetching.

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

Both the authoritative count pass and the inspection pass remain metadata-only:

- no single-object promisor fetch;
- no batch promisor fetch;
- no checkout or worktree mutation;
- no index/ref mutation;
- no promisor-state mutation.

## Regression coverage

Focused tests cover:

- an ordinary repository where a present local blob must be subtracted from the
  numeric count;
- `allow-promisor`, plain `print`, and `print-info` over a real foreign
  `blob:none` promise with zero fetches and unchanged promisor state;
- `--objects-edge --boundary --max-count --count`, proving explicit edges are
  excluded while a distinct limit-induced boundary and its snapshot are counted;
- `--reverse` with `--objects-edge` but no explicit exclusion edge, proving a
  boundary remains part of the authoritative count regardless of textual order;
- continued rejection of `-z + --filter=blob:none`.

Phase247 changes no object format, tree serialization, pack format, wire
protocol, ref/index/worktree format, or promisor identity representation.
