# Phase 236 — Limit-induced promisor object boundaries

Phase 236 removes the deliberate Phase235 restriction on combining:

```text
pygit rev-list --objects --boundary --missing=allow-promisor
```

with `--skip` and `--max-count`.

## Native Git semantics

`--boundary` renders excluded commits immediately outside the visible commit set.
Output limits can therefore create a boundary even when no explicit negative
revision exists. With `--objects`, native Git also includes the boundary commit's
own tree/blob snapshot in object traversal. It does not recursively include the
boundary's older parent history.

For example, a linear history `c1 <- c2 <- c3` with `--max-count=1 HEAD`
conceptually emits:

```text
c3
-c2
<tree/blob closure of c3>
<tree/blob closure of c2>
```

`--skip=1 --max-count=1` instead selects `c2` and makes `c1` the boundary. A
plain `--skip=1` that still walks the tail does not make skipped newer commit
`c3` a boundary because boundaries are parents outside the visible set.

`--reverse` reverses the commit/boundary stream, and native object traversal
follows that final stream order when choosing first-path object presentation.
`--count` counts selected commits, boundary commit records, and present snapshot
objects together. Expected unresolved promisor objects remain silently omitted.

## Implementation

Phase232's metadata-only `promisor_object_inventory()` now accepts an optional
`snapshot_commits` sequence. This overrides only the tree/blob snapshot traversal
order; selected commit identities still come from the normal `rev-list`
selection.

The Phase236 allow-promisor boundary path:

1. computes selected + boundary commits through the existing
   `rev_list_boundary()` helper using the real `skip`/`max_count` values;
2. uses that final commit/boundary stream as `snapshot_commits`;
3. walks exactly those commit snapshots without materializing promised blobs;
4. applies the existing explicit-negative/common-ancestry object-closure
   subtraction afterward;
5. renders boundary commits with `-` while keeping every visible object identity
   in the local SHA-256 domain.

This distinction is important: a limit-induced boundary contributes its snapshot,
while an explicitly excluded revision still subtracts its complete object closure.

## Safety and compatibility

- no single-object or batch promisor fetch is performed;
- `.pygit/promisor.json` remains unchanged;
- unresolved upstream SHA-1 identities never appear as pygit object IDs;
- no protocol, pack, tree serialization, ref, index, or worktree format changes;
- existing `--boundary + --objects-edge` rejection remains unchanged;
- ordinary repositories continue through the same inventory and rendering rules.

## Verification

Focused Phase236 tests use a real foreign `blob:none` three-commit history with
three distinct snapshots. They cover:

- `--max-count=1` limit-induced boundary snapshot closure;
- `--skip=1 --max-count=1`;
- pure `--skip=1` without a newer boundary;
- `--reverse` snapshot ordering;
- aggregate `--count` framing;
- explicit revision exclusion still subtracting boundary snapshot closure;
- unchanged promisor state and zero single/batch network fetching.
