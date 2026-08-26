# `checkout-index` plumbing

Phase 56 adds an index-to-worktree plumbing path that complements `update-index`, `ls-files`, and `read-tree`. Phase 126 extends that path to the multi-stage conflict index introduced in Phase 124, so base/ours/theirs blobs can be inspected without resolving the conflict.

## CLI

```console
pygit checkout-index path/to/file
pygit checkout-index --force path/to/file
pygit checkout-index --all
pygit checkout-index --all --prefix=export/

# Conflict-stage extraction
pygit checkout-index --stage=1 --prefix=base/ conflict.txt
pygit checkout-index --stage=2 --prefix=ours/ conflict.txt
pygit checkout-index --stage=3 --prefix=theirs/ conflict.txt
pygit checkout-index --all --stage=2 --prefix=ours/
```

The command materializes stored index objects without moving `HEAD`, updating refs, or mutating the index. Stage 0 remains the default, preserving the historical behavior. Explicit `--stage` values select:

- `0` — normal staged entry (default);
- `1` — merge base;
- `2` — ours;
- `3` — theirs.

Only entries present at the requested stage participate in pathspec matching or `--all`. This makes it safe to inspect an unmerged side without accidentally falling back to a different stage.

- Explicit paths accept exact files, directory prefixes, and glob patterns.
- `--all` writes every index entry at the selected stage and cannot be combined with explicit paths.
- Existing paths are protected by default; `--force` replaces files and symlinks, but never directories.
- `--prefix` writes beneath a repository-relative destination while preserving each indexed path. Using separate prefixes is convenient when comparing stages 1, 2, and 3 side by side.
- Mode `100755` restores executable bits.
- Mode `120000` restores symbolic links on platforms that support them.
- Mode `160000` submodule entries are rejected because this plumbing does not materialize nested repositories.
- Targets outside the repository or inside `.pygit` are rejected.

## Python API

```python
from pygit import checkout_index

written = checkout_index(
    repo,
    ["src"],
    force=True,
    prefix="export",
)

ours = checkout_index(
    repo,
    ["conflict.txt"],
    stage=2,
    prefix="ours",
)
```

The function returns the filesystem paths it wrote. It does not mutate the index, refs, or object database.

## Compatibility notes

The default `stage=0` behavior is intentionally unchanged from Phase 56. Phase 126 supports selecting one stage at a time; Git's `checkout-index --stage=all --temp` workflow is outside this phase because pygit does not yet expose checkout-index temporary-file mapping output.
