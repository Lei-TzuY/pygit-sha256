# Phase 127 — reject unmerged indexes in `write-tree`

Phase 124 made index stages 1, 2, and 3 persistent first-class records. Phase 127 closes the corresponding tree-construction safety gap: `pygit write-tree` now refuses to serialize an index that still contains any unmerged stage entries instead of silently ignoring them and writing a stage-0-only tree.

## Why this matters

`Index.all_entries()` intentionally defaults to the backward-compatible stage-0 view. Before this phase, `write_tree()` consumed that view directly. A low-level conflict created with `update-index --index-info` could therefore contain stages 1/2/3 while `write-tree` quietly omitted those records and produced a tree from whatever stage-0 entries happened to coexist with them.

That is unsafe because a tree object represents a resolved snapshot. An unresolved index must not be converted into a commit-ready tree by dropping conflict state.

## New invariant

Before validating object IDs, filtering a prefix, or creating any tree object, `write_tree()` collects the unique paths represented by stages 1-3. If any exist it raises:

```text
cannot write tree with unmerged index entries: PATH[, PATH...]
```

Paths are deterministic and sorted, and each path appears once regardless of how many conflict stages it carries.

The guard applies even with:

- `--missing-ok`;
- `--prefix=<directory>`;
- conflicts outside the selected prefix;
- stage records whose referenced objects are intentionally missing.

This matches native Git's safety boundary: prefix selection does not make an otherwise unmerged index writable.

## Atomicity

The rejection happens before `Repository._build_tree_from_entries()` is called. Therefore a failed `write-tree` does not publish partial or incidental tree objects and does not modify the index. After the path is resolved to one stage-0 entry, normal tree construction succeeds again.

## Scope boundary

This phase protects the low-level `write-tree` plumbing path. High-level merge/cherry-pick/rebase state and future porcelain-wide migration to the multi-stage index remain separate work; `commit-tree` is unaffected because it accepts an already-created tree object rather than reading the index.

## Regression coverage

`tests/test_phase127.py` covers:

- rejection of a three-stage conflict before any new object is written;
- `--prefix` and `--missing-ok` being unable to hide conflict stages;
- successful tree creation after conflict resolution replaces stages 1-3 with stage 0;
- installed CLI error behavior, deterministic path ordering, and one diagnostic entry per conflicted path.
