# Phase 220 — Path-aware promisor reset batching

Phase220 extends partial-clone demand fetching to `Repository.reset_paths()` without defeating path-limited semantics.

## Problem

The historical `reset_paths()` implementation calls `_commit_tree_entries()` before it applies pathspecs. On a filtered foreign tree, flattening the complete target tree consumes every `TreeEntry.sha`. Unresolved promised blobs therefore materialize even when the user resets only one file or directory.

That behavior is especially costly for `blob:none` repositories because an index-only operation can accidentally fetch unrelated worktree content.

## Behavior

For repositories with unresolved promisor state, Phase220:

- resolves the requested target commit without flattening its complete tree;
- traverses only directories that can contribute to pygit's existing literal file/directory pathspecs;
- validates every supplied pathspec against the union of target-tree matches and current index matches before network or index mutation;
- batches only unresolved promised blobs that are actually selected by the reset;
- preserves the Phase213 single-object materialization seam when exactly one promised object is required;
- supports index-only deletion when a selected path exists in the index but not in the target commit, with no unrelated object fetch;
- deduplicates overlapping pathspecs;
- updates only the index and never touches HEAD or the worktree, matching the existing `reset_paths()` contract;
- delegates ordinary non-promisor repositories to the historical implementation unchanged.

## SHA-256-native identity

A foreign promised tree entry initially carries its original native Git SHA-1 identity. pygit's persistent index is SHA-256-native, so a target blob selected by `reset_paths()` must be materialized before that index entry can be written. The fetched blob is imported under its real content-derived local SHA-256 object ID. No surrogate SHA-256 identifiers are created.

Unselected promises remain unresolved and continue to carry only their native identity at the interoperability boundary.

## Git compatibility

Git path-limited reset updates selected index entries from a target tree without moving HEAD or modifying the working tree. Phase220 preserves pygit's established exact-file/directory-subtree pathspec language while making partial-clone object demand obey that selection boundary.

## Coordination

Phase219 / PR #196 already made `checkout_paths()` pathspec-aware. Phase220 reuses the same retained-tree traversal primitive for the symmetric index-only reset operation and stacks directly on Phase219 exact head `f61f0f578ee59a3620fe26037df6532b0d604766`.

## Verification targets

Focused regression coverage checks:

- directory reset batches exactly the selected promised blobs;
- a single selected promise keeps the single-object materializer seam;
- all pathspecs are validated before network or index mutation;
- index-only deletion fetches no unrelated promised objects;
- overlapping pathspecs deduplicate object requests;
- ordinary repositories remain on the historical `reset_paths()` implementation.
