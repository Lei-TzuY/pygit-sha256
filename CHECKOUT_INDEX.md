# `checkout-index` plumbing

Phase 56 adds an index-to-worktree plumbing path that complements `update-index`, `ls-files`, and `read-tree`. Phase 126 extends that path to the multi-stage conflict index introduced in Phase 124, so base/ours/theirs blobs can be inspected without resolving the conflict. Phase 148 adds temporary extraction and Git-style `--stage=all` mapping output for merge-tool workflows.

## CLI

```console
pygit checkout-index path/to/file
pygit checkout-index --force path/to/file
pygit checkout-index --all
pygit checkout-index --all --prefix=export/

# One conflict stage into a normal destination
pygit checkout-index --stage=1 --prefix=base/ conflict.txt
pygit checkout-index --stage=2 --prefix=ours/ conflict.txt
pygit checkout-index --stage=3 --prefix=theirs/ conflict.txt

# One stage into a unique temp file
pygit checkout-index --temp --stage=2 conflict.txt

# Every available unmerged stage; --stage=all implies --temp
pygit checkout-index --stage=all conflict.txt
pygit checkout-index --stage=all --all
pygit checkout-index --stage=all -z conflict.txt
```

The command materializes stored index objects without moving `HEAD`, updating refs, or mutating the index. Stage 0 remains the default, preserving the historical behavior. Explicit numeric stages select:

- `0` — normal staged entry (default);
- `1` — merge base;
- `2` — ours;
- `3` — theirs.

`--stage=all` is only meaningful for unmerged paths and automatically enables temporary-file mode. Paths with only stage 0 are omitted.

## Normal checkout mode

Only entries present at the requested numeric stage participate in pathspec matching or `--all`.

- Explicit paths accept exact files, directory prefixes, and glob patterns.
- `--all` writes every index entry at the selected stage and cannot be combined with explicit paths.
- Existing paths are protected by default; `--force` replaces files and symlinks, but never directories.
- `--prefix` writes beneath a repository-relative destination while preserving each indexed path.
- Mode `100755` restores executable bits.
- Mode `120000` restores symbolic links on platforms that support them.
- Mode `160000` submodule entries are rejected because this plumbing does not materialize nested repositories.
- Targets outside the repository or inside `.pygit` are rejected.

## Temporary-file mode

`--temp` writes unique files directly beneath the repository top level instead of touching the tracked destination. Each generated basename begins with `.merge_file_`, contains no path separators or whitespace, and is printed to stdout together with the tracked path.

For a numeric stage the mapping format is:

```text
TEMP<TAB>PATH<RS>
```

For `--stage=all` the format is:

```text
STAGE1TEMP SP STAGE2TEMP SP STAGE3TEMP<TAB>PATH<RS>
```

A missing conflict side is represented by `.`. For example, a modify/delete conflict may print:

```text
.merge_file_A .merge_file_B .<TAB>gone.txt
```

The record separator `<RS>` is newline by default and NUL with `-z`. In temporary mode `--prefix` is accepted but ignored, matching Git's behavior: temp names remain relative to the repository top level. `--force` is irrelevant because names are uniquely created.

Temporary extraction has two additional safety properties:

1. every selected object is validated before the first temp file is created;
2. if a later filesystem write fails, temp files already created by that call are removed.

Symlink index entries are written as regular temp files containing the link target bytes. This lets external merge tools consume all stages uniformly.

## Python API

```python
from pygit import checkout_index, checkout_index_temp

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

records = checkout_index_temp(
    repo,
    ["conflict.txt"],
    stage="all",
)
base_file = records[0].file_for(1)
ours_file = records[0].file_for(2)
theirs_file = records[0].file_for(3)
```

`checkout_index_temp()` returns `CheckoutTempRecord` objects. Each record contains the tracked path and the generated `(stage, Path)` pairs. Missing stages are absent and `file_for(stage)` returns `None`.

Neither API mutates the index, refs, object database, merge/cherry-pick/rebase state, or tracked destination paths.

## Compatibility notes

The default stage-0 behavior remains unchanged from Phase 56 and numeric stage extraction remains unchanged from Phase 126. Phase 148 closes the earlier `--stage=all --temp` compatibility boundary while intentionally leaving `--stdin`, skip-worktree handling, `--index`, `--no-create`, and quiet-mode behavior for separate phases.
