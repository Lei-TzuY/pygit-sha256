# Phase 60: merge-tree plumbing

Phase 60 adds a side-effect-free three-way merge primitive for pygit's SHA-256 object model.

## CLI

```bash
pygit merge-tree OURS THEIRS
pygit merge-tree --messages OURS THEIRS
```

A clean merge prints the resulting 64-hex tree object ID and exits 0. A conflicted merge prints one `CONFLICT<TAB>path` record per conflicted path and exits 1. `--messages` additionally reports the selected merge base and whether the result is clean.

## Python API

```python
from pygit import merge_tree

result = merge_tree(repo, "main", "feature")
if result.clean:
    print(result.tree_oid)
else:
    print(result.conflicts)
```

The operation resolves both commit-ish arguments, selects the best common ancestor, merges their flattened trees, and writes clean merged blob/tree objects. It does not move HEAD, alter refs, rewrite the index, or modify the working tree.

## Merge rules

Identical entries are kept directly. If only one side differs from the base, that side wins. Independent text-line changes are merged through the repository's existing three-way line merge. Delete/modify conflicts, incompatible mode changes, differing gitlinks, overlapping text edits, and differing binary edits are reported as conflicts rather than guessed.

Histories with multiple equally-good merge bases are currently rejected explicitly; pygit does not yet synthesize recursive virtual merge bases.
