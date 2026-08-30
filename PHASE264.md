# Phase 264 — `rev-list --in-commit-order --filter=blob:none`

Phase264 composes the metadata-only ordered object traversal introduced in
Phase259–263 with Git's `blob:none` object filter.

## Scope

The ordered adapter now accepts:

```text
pygit rev-list --objects --in-commit-order --filter=blob:none HEAD
pygit rev-list --objects --in-commit-order --reverse --filter=blob:none HEAD
pygit rev-list --objects --in-commit-order --boundary --filter=blob:none HEAD
pygit rev-list --objects-edge --in-commit-order --filter=blob:none A..B
pygit rev-list --objects --in-commit-order --count --filter=blob:none HEAD
pygit rev-list --objects --in-commit-order -z --filter=blob:none HEAD
```

`--filter-provided-objects` is also accepted with `blob:none`. In pygit's
current commit-rooted rev-list model the provided roots are commits, so applying
`blob:none` to those roots does not change their membership.

`--filter-print-omitted`, other filter families, and disk-usage composition
remain deferred to dedicated phases.

## Git compatibility

Git 2.55 documents `--filter=<filter-spec>` as useful with an `--objects*`
traversal and defines `blob:none` as omitting all blobs. It separately defines
`--in-commit-order` as printing trees and blobs in commit order, after the first
commit that references them. The combination therefore keeps commit/tree
first-seen ordering while removing blob records.

A deterministic native SHA-256 Git 2.47.3 fixture was probed before the
implementation. With three commits that successively add `a.txt`, `b.txt`, and
`c.txt`, native Git produced:

- normal `--in-commit-order --filter=blob:none`: each commit followed by its
  first-seen root tree, with no blob IDs;
- `--reverse`: the same commit/tree pairs in the reversed selected-commit order;
- `--boundary --max-count=1`: selected commit/tree followed by the boundary
  commit/tree;
- `--count`: `6` for three commits plus three root trees.

Phase264 follows those observable ordering and count rules.

## Structured filtering

The implementation does not render ordinary output and then delete blob lines.
It first builds the same `PromisorObjectInventoryEntry` sequence used by the
ordered traversal, then removes entries whose `type_name == "blob"` before any
renderer sees them.

This means line output, `--count`, boundary framing, object-edge framing, and
Phase263's NUL renderer all consume the same filtered ordered inventory.
Traversal itself is not pruned: commits and trees are still walked so later
first-seen object positions are computed correctly.

## Partial-clone behavior

An unresolved promised blob is represented in inventory by its native SHA-1 and
promisor type metadata. Because `blob:none` filters that entry before ordinary
missing-object validation:

- no lazy single-object fetch is needed;
- no batch fetch is needed;
- ordinary traversal can succeed without an explicit `--missing` policy when
  every unresolved object encountered is a filtered blob;
- `--missing=print` / `print-info` do not report blobs that the object filter has
  already removed;
- promisor state is not mutated merely to classify the filter.

If a future partial traversal contains a missing non-blob object, existing
missing-object rules remain authoritative.

## SHA-256-native identity boundary

Present commits, trees, boundary records, and object edges remain genuine local
64-hex SHA-256 object IDs. Filtered promised blobs are omitted before
presentation, so their upstream/native 40-hex SHA-1 IDs do not leak into normal
repository-visible output. No padding, translation, or surrogate SHA-256 value
is synthesized.

Explicit missing metadata remains the only foreign-identity channel for objects
that survive filtering and are still unresolved.

## Deliberate deferrals

Phase264 intentionally does not implement:

- `--in-commit-order --filter-print-omitted`;
- `--in-commit-order --filter=object:type=...`;
- `--in-commit-order --filter=blob:limit=...`;
- `--in-commit-order --disk-usage`.

The next filter phase should reuse this same structured ordered-inventory filter
point rather than adding a second walker or text post-processor.

## Verification

Focused Phase264 regression coverage includes normal and reverse ordering,
boundaries, object edges, counts, NUL framing, `--filter-provided-objects`,
partial-clone ordinary traversal with filtered promises, explicit print-info,
zero intentional materialization, unchanged promisor state, and validation of
still-deferred filter families.

The full repository test suite is verified by the exact-head GitHub Actions
Python 3.9 and 3.13 matrix because the local execution environment cannot resolve
`github.com` for a repository clone.
