# Low-level diff plumbing

Phase 58 adds record-oriented comparisons across pygit's three snapshot layers:

- `diff-tree`: tree/commit vs tree/commit, or one commit vs its first parent
- `diff-index`: tree/commit vs index (`--cached`) or tracked working-tree state
- `diff-files`: index vs tracked working-tree state

All object IDs are pygit's native 64-hex SHA-256 values. The commands are metadata plumbing: they do not generate patch hunks and do not mutate refs, the index, objects, or the worktree.

## CLI

```console
pygit diff-tree HEAD~1 HEAD
pygit diff-tree --root <root-commit>
pygit diff-tree --name-status HEAD~1 HEAD
pygit diff-tree -z --name-only HEAD~1 HEAD -- src/

pygit diff-index --cached HEAD
pygit diff-index HEAD -- src/
pygit diff-index --name-status HEAD

pygit diff-files
pygit diff-files --name-only
pygit diff-files --exit-code -- src/
pygit diff-files --quiet
```

Pathspecs are supplied after `--`. Literal paths match themselves and descendants; patterns containing `*`, `?`, or `[` use shell-style matching.

## Output

Raw output is the default:

```text
:<old-mode> <new-mode> <old-oid> <new-oid> <status>\t<path>
```

Added or deleted sides use mode `000000` and a 64-zero object ID. Status is:

- `A`: added
- `D`: deleted
- `M`: content or ordinary mode change
- `T`: file-kind change, for example regular file to symlink

`--name-status` emits `<status>\t<path>`, while `--name-only` emits paths only. `-z` switches record termination from newline to NUL.

`--exit-code` returns 1 when differences exist. `--quiet` suppresses output and also returns 1 when differences exist.

## Tree resolution

`diff-tree` and `diff-index` use the shared Phase 57 revision resolver. That means tree-ish inputs may use:

- loose or packed refs
- full SHA-256 IDs
- unique 4+ hexadecimal abbreviations
- packed-only abbreviated objects after `repack`
- ancestry expressions such as `HEAD~2`
- annotated tags that peel to commits or trees

Tree contents are flattened recursively before comparison, so `-r` is accepted by `diff-tree` for compatibility but recursion is already the default.

With one commit argument, `diff-tree COMMIT` compares the commit to its first parent. A root commit produces no records unless `--root` is supplied, in which case it is compared with an empty tree.

## Index and worktree semantics

`diff-index --cached TREE` compares the named tree directly with the JSON index.

Without `--cached`, `diff-index TREE` compares the named tree with the current filesystem state of paths known by either the tree or index. Untracked paths are intentionally omitted.

`diff-files` compares only index paths with their current filesystem state. It detects:

- content changes
- owner-executable bit changes (`100644` / `100755`)
- symlink target changes using the symlink target bytes
- deletions
- tracked file replaced by directory/type changes

Pygit does not yet model nested repository HEAD state for gitlinks. Therefore an existing directory corresponding to an index/tree mode `160000` retains the known gitlink object ID for comparison; a missing gitlink is still detected as deleted.

## Safety and validation

Before reading worktree paths, the diff layer rejects malformed repository paths such as absolute paths, `..`, empty components, backslashes, and NUL bytes. It also resolves the parent directory and refuses a path whose symlinked parent escapes the repository root.

Index snapshots validate that:

- regular/executable/symlink entries reference blobs
- mode `160000` entries reference commits
- referenced objects actually exist
- unsupported index modes fail loudly

## Python API

```python
from pygit import diff_tree, diff_index, diff_files

changes = diff_tree(repo, "HEAD~1", "HEAD")
staged = diff_index(repo, "HEAD", cached=True)
unstaged = diff_files(repo)

for change in changes:
    print(change.status, change.path, change.old_oid, change.new_oid)
```

`DiffEntry` is immutable and contains `path`, `old_mode`, `new_mode`, `old_oid`, `new_oid`, and `status`. `format_diff_entries()` renders record sequences in raw, name-status, name-only, newline, or NUL-delimited forms.
