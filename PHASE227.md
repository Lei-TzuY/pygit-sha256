# Phase 227 — Promisor-aware blame batching

Phase227 removes the remaining obvious partial-clone demand-fetch waterfall from `Repository.blame()` while preserving pygit's SHA-256-native repository model and the historical blame implementation.

## Problem

`Repository.blame(path)` first walks commit metadata with `log()`, then for every commit and its first parent calls `_commit_tree_entries()` before reading the selected file's blob contents. In a filtered partial clone, unresolved foreign tree entries hold only native Git SHA-1 object ids until the promised blobs are materialized.

Because `_commit_tree_entries()` flattens the complete snapshot into local `(path, SHA-256, mode)` entries, the historical blame path can otherwise fault in omitted blobs one at a time. A long history therefore risks one network request per promised object.

## Change

Phase227 wraps `Repository.blame()` without modifying `repo.py`:

1. read the existing promisor state;
2. run the same metadata-only `log()` traversal that blame already uses;
3. preserve the historical empty-history and missing-worktree-path error ordering before any network request;
4. collect every commit snapshot plus first-parent snapshot that the historical blame algorithm will flatten;
5. deduplicate unresolved promised blobs through the existing history collector;
6. materialize the union once through the established multi-promisor materializer;
7. call the original blame implementation unchanged.

Single-object materialization retains the Phase213 compatibility seam. Multi-object demand uses the existing bulk request path and therefore inherits Phase221/222 promisor fallback, primary-promisor-last ordering, batch shrinking, and per-remote `serverOption` behavior.

## Why complete snapshots are fetched

The semantic blame target is one file, but the current implementation calls `_commit_tree_entries()` for every inspected commit and parent. That helper requires real local SHA-256 ids for every retained tree entry. A missing foreign blob has only its native SHA-1 promise until its contents are available.

Phase227 therefore does **not** claim path-only blame materialization. Narrowing demand to only the selected file would require a mixed native/local tree comparison layer or a path-specific tree lookup API that can avoid flattening unrelated entries. Until that exists, the correct improvement is to collapse the existing N-request waterfall into one deduplicated bulk fetch without inventing surrogate SHA-256 identities.

## Compatibility

- ordinary repositories remain network-free;
- already-resolved partial histories perform no additional fetch;
- metadata-only `log()` remains blob-free;
- `line_range` still affects only returned blame lines, not attribution semantics;
- empty repositories still report `No commits found` before any network attempt;
- missing worktree paths still raise the historical `FileNotFoundError` before any network attempt;
- attribution, date formatting, author formatting, path normalization, and returned strings remain owned by the original `Repository.blame()` implementation;
- no protocol, pack, tree serialization, ref, index, or object identity format changes are introduced.

## SHA-256-native boundary

Promisor requests continue to use native Git SHA-1 only at the interoperability boundary. Materialized contents are written under their real content-derived SHA-256 ids. Existing foreign-tree identity remains stable and no surrogate object ids are created.

## Tests

`tests/test_phase227.py` covers:

- one bulk request for a real foreign root→child blame history;
- correct child/root line attribution after bulk materialization;
- inclusion of unrelated promised entries required by complete snapshot flattening;
- ordered remote `serverOption` forwarding;
- `line_range` output slicing with a single bulk request;
- missing-path and empty-history errors before network activity;
- ordinary-repository blame remaining network-free.
