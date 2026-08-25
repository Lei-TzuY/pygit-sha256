# Phase 60: merge-tree plumbing

Phase 60 adds tree-only three-way merge plumbing for pygit's SHA-256 object model.

## CLI

```bash
pygit merge-tree OURS THEIRS
pygit merge-tree --write-tree OURS THEIRS
pygit merge-tree --merge-base BASE OURS THEIRS
pygit merge-tree --messages OURS THEIRS
pygit merge-tree --name-only OURS THEIRS
pygit merge-tree --allow-unrelated-histories OURS THEIRS
```

A clean merge prints the resulting 64-hex tree object ID and exits 0. A conflicted merge exits 1 and prints `CONFLICT (reason)<TAB>path` records, or only paths with `--name-only`. `--messages` also reports the selected merge base and clean status. `--write-tree` is accepted for modern Git compatibility; this focused implementation always materializes a clean result tree.

## Python API

```python
from pygit import MergeConflict, MergeTreeResult, merge_tree

result = merge_tree(repo, "main", "feature")
if result.clean:
    print(result.tree_oid)
else:
    for conflict in result.conflicts:
        print(conflict.reason, conflict.path)
```

`merge_tree()` resolves commit-ish values through the shared revision layer and automatically selects the unique best common ancestor. Callers may override it with `base=...`. Histories with no common ancestor are rejected unless `allow_unrelated_histories=True` is explicit.

## Merge rules

Identical entries are kept directly. If only one side differs from the base, that side wins. Independent UTF-8 text-line changes are merged through the repository's existing three-way line merger. Ambiguous cases are surfaced rather than guessed, including:

- add/add with different objects
- modify/delete
- overlapping text edits
- binary edits
- incompatible file-type changes
- simultaneous symlink-target changes
- differing gitlinks
- directory/file path collisions

Conflict records retain base/ours/theirs object IDs and modes for tooling.

## State guarantees

The command never moves HEAD, updates refs, rewrites the index, or modifies the working tree. A clean merge may add content-addressed merged blob/tree objects to `.pygit/objects`; conflicted merges do not write pending auto-merged blobs or a result tree.

Criss-cross histories with multiple equally-good merge bases are rejected explicitly. Pass a deliberate `--merge-base` / `base=` rather than silently selecting an arbitrary ancestor.
