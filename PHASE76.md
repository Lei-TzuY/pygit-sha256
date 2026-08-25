# Phase 76 — Revision-aware `ls-tree`

Phase 76 replaces the installed legacy `ls-tree` path with a modular, read-only tree inspection layer that uses the shared SHA-256 revision resolver introduced in Phase 57.

## Why this phase exists

The older command exposed only `-r`, `--name-only`, and a basic tree-ish argument through `Repository.ls_tree()`. It predated packed-object-aware abbreviations, ancestry expressions, annotated-tag peeling, `REV:path`, gitlink-aware modes, structured output, and modern pathspec handling.

The installed command now routes through `pygit.ls_tree` before the legacy argparse stack. The old repository method remains untouched for source compatibility.

## Python API

```python
from pygit import ls_tree, format_ls_tree

entries = ls_tree(repo, "HEAD", recursive=True)
for entry in entries:
    print(entry.mode, entry.object_type, entry.oid, entry.path)

raw = format_ls_tree(repo, entries, nul_terminated=True)
```

`LsTreeEntry` contains `mode`, `object_type`, full 64-hex SHA-256 `oid`, and tree-relative `path`. Traversal and presentation are deliberately separate.

## Tree-ish resolution

`TREE-ISH` uses the shared revision resolver and therefore supports loose or packed refs, full and unique abbreviated SHA-256 IDs, ancestry expressions, annotated tags, direct tree objects, `REV:path`, and typed peel expressions already supported by `revision.py`.

A blob or other non-tree-ish root fails cleanly.

## Mode and type model

| mode | type |
| --- | --- |
| `040000` | `tree` |
| `100644` | `blob` |
| `100755` | `blob` |
| `120000` | `blob` |
| `160000` | `commit` |

Gitlinks therefore appear as commit entries rather than being mislabeled as blobs. Unknown modes, malformed object IDs, and unsafe tree entry names fail instead of being emitted as trusted paths.

## CLI

```text
pygit ls-tree [OPTIONS] [TREE-ISH] [PATHSPEC...]
pygit ls-tree [OPTIONS] [TREE-ISH] -- [PATHSPEC...]
```

Supported options:

- `-r`, `--recursive` — recurse into subtrees;
- `-d`, `--directory` — report tree entries only;
- `-t`, `--show-trees` — include tree entries during recursive output;
- `--name-only` — output paths only;
- `--object-only` — output object IDs only;
- `--format=FORMAT` — use `%(objectmode)`, `%(objecttype)`, `%(objectname)`, and `%(path)`;
- `--abbrev[=N]` — print unique object prefixes, default minimum 12;
- `-z` — terminate records with NUL.

Default output is `<mode> <type> <64-hex-object-id><TAB><path>`.

## Recursive and pathspec semantics

Without `-r`, direct children are reported. A nested literal pathspec such as `sub/file.txt` performs only the minimum subtree traversal needed to reach that path. With `-r`, file/gitlink entries are emitted recursively and tree entries are hidden unless `-t` or `-d` is requested. Gitlinks (`160000`) are leaves and are never traversed.

Pathspecs are tree-relative. Literal directory pathspecs select that directory and, when recursive, its descendants. Shell-style `fnmatch` patterns are supported. Traversal is pruned before opening subtrees that cannot contain a literal/static-prefix match, so a selected subtree does not force unrelated malformed trees to be read.

Absolute paths, backslashes, NULs, empty components, `.` components, and `..` components are rejected. This is intentionally a compact educational subset rather than native Git's full pathspec-magic language.

## Formatting

`format_ls_tree()` returns bytes so newline and NUL output share one deterministic implementation. Custom formats support the four documented atoms plus `%%` for a literal percent sign. Unknown atoms fail. `--name-only`, `--object-only`, and `--format` are mutually exclusive.

When `--abbrev` is requested, locally present objects use shared uniqueness-aware abbreviation logic. A deliberately missing leaf can still display the recorded prefix, while recursive traversal of a missing or wrong-type tree fails.

## Safety and compatibility

- read-only: no ref, index, worktree, reflog, or object-store mutation;
- annotated-tag cycles and defensive tree cycles are rejected;
- selected tree children must deserialize as trees before traversal;
- irrelevant subtrees can be skipped by pathspec pruning;
- installed `pygit ls-tree` no longer depends on the legacy global argparse parser;
- legacy `Repository.ls_tree()` remains available for source compatibility.

## Regression coverage

Phase 76 covers SHA-256 default records, regular/executable/symlink/tree/gitlink modes, recursive `-t/-d`, annotated tags, short SHA, direct trees, `REV:path`, packed-only resolution after repack, literal/glob pathspecs, malformed-subtree pruning, formatting/NUL/abbreviation, invalid roots/modes/names, and installed CLI routing.
