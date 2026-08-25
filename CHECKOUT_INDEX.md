# `checkout-index` plumbing

Phase 56 adds an index-to-worktree plumbing path that complements `update-index`, `ls-files`, and `read-tree`.

## CLI

```console
pygit checkout-index path/to/file
pygit checkout-index --force path/to/file
pygit checkout-index --all
pygit checkout-index --all --prefix=export/
```

The command reads stage-0 entries from pygit's JSON index and materializes their stored objects without moving `HEAD` or updating refs.

- Explicit paths accept exact files, directory prefixes, and glob patterns.
- `--all` writes every index entry and cannot be combined with explicit paths.
- Existing paths are protected by default; `--force` replaces files and symlinks, but never directories.
- `--prefix` writes beneath a repository-relative destination while preserving each indexed path.
- Mode `100755` restores executable bits.
- Mode `120000` restores symbolic links on platforms that support them.
- Mode `160000` submodule entries are rejected because this phase does not materialize nested repositories.
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
```

The function returns the filesystem paths it wrote. It does not mutate the index, refs, or object database.
