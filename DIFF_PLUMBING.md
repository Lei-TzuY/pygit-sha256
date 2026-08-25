# Low-level diff plumbing

Phase 59 adds record-oriented comparisons across pygit's three snapshot layers:

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

Added or deleted sides use mode `000000` and a 64-zero object ID. Status is `A`, `D`, `M`, or `T` for add, delete, modification/mode change, or file-kind change.

`--name-status` emits `<status>\t<path>`, while `--name-only` emits paths only. `-z` switches record termination from newline to NUL. `--exit-code` returns 1 when differences exist; `--quiet` suppresses output and also returns 1 when differences exist.

## Resolution and graph semantics

`diff-tree` and `diff-index` use the shared revision resolver, so inputs may be loose or packed refs, full SHA-256 IDs, unique abbreviations including packed-only objects after `repack`, ancestry expressions such as `HEAD~2`, and annotated tags that peel to commits or trees.

Tree contents are flattened recursively; `diff-tree -r` is accepted for compatibility but recursion is already the default.

With one commit argument, `diff-tree COMMIT` compares the commit with its first parent. A root commit produces no records unless `--root` is supplied. Commits listed in `.pygit/shallow` are treated as graph roots even if the stored commit object still names parents. Annotated tags are peeled before this single-commit comparison.

## Index and worktree semantics

`diff-index --cached TREE` compares the named tree directly with the JSON index. Without `--cached`, it compares the named tree with current filesystem state for paths known by either the tree or index; untracked paths are intentionally omitted.

`diff-files` compares only index paths with their current filesystem state and detects content changes, owner-executable bit changes (`100644` / `100755`), symlink target changes, deletions, and file-kind changes.

Pygit does not yet model nested repository HEAD state for gitlinks. An existing directory corresponding to mode `160000` therefore retains the known gitlink OID for comparison; a missing gitlink is still detected as deleted.

## Safety

Before reading worktree paths, the diff layer rejects absolute paths, `..`, empty components, backslashes, and NUL bytes. It resolves the parent directory and rejects symlink-parent escapes outside the repository root.

Index snapshots validate supported modes, referenced object existence, blob types for regular/executable/symlink entries, and commit types for gitlinks.

## Python API

```python
from pygit import diff_tree, diff_index, diff_files

changes = diff_tree(repo, "HEAD~1", "HEAD")
staged = diff_index(repo, "HEAD", cached=True)
unstaged = diff_files(repo)
```

`DiffEntry` is immutable and contains `path`, `old_mode`, `new_mode`, `old_oid`, `new_oid`, and `status`. `format_diff_entries()` renders raw, name-status, name-only, newline, or NUL-delimited output.
