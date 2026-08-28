# Phase219 — path-aware promisor checkout

Phase219 extends the partial-clone worktree batching stack to path-limited checkout without materializing unrelated promised blobs.

## Problem

`Repository.checkout_paths(paths, target)` historically builds a flat dictionary for the complete target tree before applying pathspecs. That is harmless for complete repositories, but a filtered foreign tree may contain unresolved native blob entries. Flattening the whole tree consumes every `TreeEntry.sha`, so restoring one path can accidentally trigger lazy fetches for unrelated promised blobs.

That defeats an important partial-clone property: a path-limited operation should fault in only the objects it actually needs.

## Implementation

Phase219 installs a narrow partial-clone override for `checkout_paths`.

- ordinary repositories continue to call the historical implementation unchanged;
- partial repositories traverse only tree directories that can contribute to the requested literal pygit pathspecs;
- unrelated blob entries are never resolved during selection;
- every pathspec is validated before network, index, or worktree side effects;
- unresolved selected native blob OIDs are deduplicated and materialized through the existing Phase214 batch materializer;
- a single selected promise still preserves Phase213's single-object fetch seam;
- after materialization, only selected paths are written to the worktree and SHA-256-native index;
- overlapping pathspecs do not duplicate network wants or returned paths.

The retained commit/tree graph is sufficient for path traversal because the current `blob:none` / `blob:limit` promisor model permits omitted blobs, not omitted required subtrees.

## Git compatibility

Git pathspecs limit checkout to a subset of the tree/worktree. The current pygit pathspec implementation is intentionally simpler than full Git wildcard/magic pathspec syntax: a path matches itself, and a directory path selects the subtree below it. Phase219 preserves those existing pygit semantics rather than silently expanding the command language.

The important compatibility improvement is object demand: path-limited checkout now materializes only objects reachable through the selected path scope instead of flattening the complete target snapshot first.

## SHA-256-native boundary

No object-format changes are introduced.

- foreign tree entries retain their original native Git SHA-1 identity in the stable promisor tree representation;
- selected promised blobs are fetched by native SHA-1 only at the protocol boundary;
- materialized blobs are written under their real content-derived local SHA-256 IDs;
- the index stores only local SHA-256 IDs;
- unselected promises remain unresolved and no surrogate SHA-256 IDs are invented.

## Coordination

Phase218 / PR #195 already implemented batching for full commit-to-worktree replacement and explicitly deferred path-limited `checkout_paths()` so it could be pathspec-aware. Phase219 stacks directly on Phase218 exact head `9a1af24131ba889b1fc0986360023e5504183688` and does not modify the full-snapshot wrapper.

## Verification focus

`tests/test_phase219.py` covers:

- selecting a directory with two promised blobs produces one batched fetch;
- an unrelated promised blob remains promised and absent from the worktree;
- one selected blob preserves the single-object materialization seam;
- invalid pathspecs fail before materialization or mutation;
- overlapping pathspecs deduplicate network wants and restored results;
- ordinary repositories stay on the historical network-free implementation.
