# Phase224 — Promisor-aware three-way batching

Phase224 removes the remaining per-object lazy-fetch waterfall from true three-way merge and replay operations in partial clones.

## Problem

pygit's merge machinery intentionally operates on SHA-256-native flat tree maps. Before `_apply_three_way()` can decide whether a path is unchanged, changed on one side, or changed on both sides, the historical implementation expands complete commit trees into `path -> (blob_sha, mode)` mappings.

For an ordinary repository every blob already has a local SHA-256 id. In a filtered partial clone, however, a retained foreign tree entry may still contain only its native Git SHA-1 promise. Accessing such entries one by one during tree flattening can trigger one demand-fetch round trip per missing blob.

The same issue appears in `_apply_cherry_pick()`, which is the shared replay primitive used by both top-level cherry-pick and rebase.

## Change

Phase224 adds a narrow prefetch layer around the existing Repository primitives rather than replacing their merge logic.

### Non-fast-forward merge

Before a merge enters the historical three-way tree flattener, pygit now collects the union of unresolved promised blobs reachable from:

- merge base, when one exists;
- current `HEAD` / ours;
- target / theirs.

That union is deduplicated and materialized through the established multi-promisor materializer in one bulk request when several objects are missing.

Up-to-date merges do not prefetch. Ordinary fast-forward merges remain owned by the Phase218 full-worktree transition wrapper, so Phase224 does not duplicate that request. A squash merge of a fast-forwardable target still uses three-way machinery and therefore participates in Phase224 batching.

### Cherry-pick and rebase replay

`Repository._apply_cherry_pick()` now receives the same prefetch treatment before it flattens:

- source parent / base, when present;
- current `HEAD` / ours;
- source commit / theirs.

Because rebase replay already calls `_apply_cherry_pick()` for each pending commit, rebase inherits the same batching policy without a second command-specific implementation.

## Why complete snapshots are materialized

This phase intentionally does not claim changed-path-only fetching. The current historical `_commit_tree_entries()` / `_tree_entries()` representation requires real local SHA-256 blob ids for every retained entry while flattening a tree. An unresolved foreign blob has no valid local SHA-256 id until its content is available.

Fetching only paths that later prove to conflict would therefore require either inventing surrogate ids or redesigning the flat-tree merge representation to carry unresolved native identities. Phase224 takes the safe improvement for the current object model: collapse N independent demand fetches into one deduplicated batch before flattening.

## Atomicity

For partial-clone merge, the wrapper repeats the existing operation/clean-worktree guards before demand fetch. A dirty worktree or conflicting in-progress operation therefore still fails before network activity.

Promise materialization occurs before the historical three-way code writes merge results, updates the index, records merge conflict state, or advances `HEAD`. A materialization failure leaves those repository/worktree structures untouched.

## Existing promisor policy preserved

Phase224 delegates actual object retrieval to the established materializer, so it automatically retains:

- Phase213 single-object fetch compatibility;
- Phase214 multi-object batching;
- Phase221 multi-promisor fallback and batch shrinking;
- Phase222 `extensions.partialClone` primary-promisor-last ordering;
- per-remote `serverOption` isolation;
- stale promisor fallback behavior.

## SHA-256-native boundary

Native SHA-1 is used only to identify promised Git objects at the interoperability boundary. Materialized blobs are stored under their real content-derived SHA-256 ids before the ordinary merge code consumes them. No surrogate object ids, alternate tree serialization, or protocol changes are introduced.

## Verification targets

`tests/test_phase224.py` covers:

- a real foreign base/ours/theirs DAG where two unresolved blobs are fetched in one bulk request before non-fast-forward merge;
- forwarding configured `remote.origin.serverOption` through that batch;
- the same real partial DAG through top-level cherry-pick with one batch;
- prefetch failure occurring before `HEAD`, index, worktree, or merge-state mutation;
- rebase replay routing through the shared `_apply_cherry_pick()` prefetch seam;
- ordinary three-way merge remaining network-free.
