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
snapshot roots, so the filter layer must use those same structured planners
rather than infer semantics from textual `-` records.

## Native Git baseline

A native SHA-256 repository was exercised directly. The observed project
contract is:

- `rev-list --objects --filter=blob:none --count HEAD` reports the filtered
  present-object count used by the existing promisor-aware object traversal;
- local blobs omitted by `blob:none` do not contribute to the integer;
- `--objects-edge --count` keeps explicit `-edge` records in the established
  adapter framing, but excluded edges do not contribute to the integer;
- genuine `--boundary` commits that remain in the structured boundary stream do
  contribute to the count;
- explicit edge/boundary overlap is deduplicated by Phase243 before the filtered
  count is derived.

Revision ordering matters when a test uses `--max-count` to create a distinct
limit boundary. Phase247 therefore gives its synthetic three-commit history
strictly increasing commit timestamps. With deterministic `c3 -> c2 -> c1`
ordering, `c1..c3 --max-count=1` keeps `c1` as the explicit edge and introduces
`c2` as the distinct limit boundary; after `blob:none`, the counted present set
is `c3`, `c2`, and their two distinct root trees.

## Implementation

Phase247 does not change `promisor_object_inventory` or revision selection.
The final count formula mirrors Phase240 directly on the structured Phase232
inventory:

1. remove only `--filter=blob:none` for the existing count presentation pass;
   this preserves established object-edge ordering and `?missing` framing;
2. parse plain `print` as the same traversal as `print-info`, and project
   `--objects-edge` to `--objects` only for structured inventory selection;
3. when `--boundary` is active, obtain the exact selected/boundary commit stream
   from Phase236's `_promisor_boundary_commits()` and use those commit ids as
   `snapshot_commits` for `promisor_object_inventory()`;
4. let the inventory perform object deduplication and explicit negative/common-
   ancestry closure subtraction exactly as in Phase236/240;
5. without `--boundary`, count every present inventory entry whose type is not
   `blob`;
6. with `--boundary`, count boundary/selected commit records plus present,
   non-blob snapshot entries, suppressing only top-level selected commit entries
   already owned by boundary presentation;
7. if `--objects-edge` is active, subtract only explicit edge/boundary overlap
   using Phase243's established overlap planner;
8. keep the existing count pass's edge and non-blob missing records, discard its
   old numeric tail, suppress promised blob missing records, and emit the newly
   computed filtered integer.

This deliberately avoids both failed shortcuts discovered during development:

- counting lines from the uncounted Phase243 presentation can miss snapshot
  contributions which Phase240 computes structurally;
- subtracting blobs from an opaque underlying count can double-apply assumptions
  about which snapshot objects that count already includes.

Instead, Phase247 asks the same boundary and inventory layers which already own
the object set and applies only one new predicate: `type_name != "blob"`.

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

Both presentation and structured counting remain metadata-only:

- no single-object promisor fetch;
- no batch promisor fetch;
- no checkout or worktree mutation;
- no index/ref mutation;
- no promisor-state mutation.

## Regression coverage

Focused tests cover:

- an ordinary repository where a present local blob must be absent from the
  numeric count;
- `allow-promisor`, plain `print`, and `print-info` over a real foreign
  `blob:none` promise with zero fetches and unchanged promisor state;
- deterministic `--objects-edge --boundary --max-count --count`, proving an
  explicit edge is excluded while a distinct limit-induced boundary and its
  non-blob snapshot remain counted;
- `--reverse` with `--objects-edge` but no explicit exclusion edge, proving a
  genuine limit-induced boundary remains part of the structured count regardless
  of textual order;
- continued rejection of `-z + --filter=blob:none`.

## CI correction

The first two full matrix runs exposed a test-fixture ordering bug. The synthetic
ordinary commits were created within the same wall-clock second, so their commit
timestamps tied and rev-list was allowed to fall back to OID ordering. Depending
on the generated OIDs, the same range test could legitimately exercise a
different selected/boundary ordering: one run produced a filtered count of `2`,
and the next produced `4` after only the expected assertion changed.

The fix is to make the fixture deterministic rather than teach production code
a false ordering rule. `_ordinary_three_commit_repo()` now passes explicit,
strictly increasing `commit_date` values (`1`, `2`, `3`) to `Repository.commit()`.
The regression can therefore assert the intended `c3 -> c2 -> c1` history and a
stable filtered count of `4` on every runner.

Phase247 changes no object format, tree serialization, pack format, wire
protocol, ref/index/worktree format, or promisor identity representation.
