# Phase 75 — Revision-aware `ls-tree`

Phase 75 replaces the installed legacy `ls-tree` path with a modular, read-only tree inspection layer that uses the shared SHA-256 revision resolver introduced in Phase 57.

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

`LsTreeEntry` contains:

- `mode`
- `object_type`
- full 64-hex SHA-256 `oid`
- repository-tree-relative `path`

Traversal and presentation are deliberately separate.

## Tree-ish resolution

`TREE-ISH` uses the shared revision resolver and therefore supports:

- loose or packed refs;
- full SHA-256 IDs;
- unique 4+ hexadecimal abbreviations, including packed-only objects;
- ancestry expressions such as `HEAD~2` and `HEAD^2`;
- annotated tags;
- direct tree objects;
- `REV:path` subtree expressions;
- typed peel expressions already supported by `revision.py`.

A blob or other non-tree-ish root fails cleanly.

## Mode and type model

Phase 75 uses the tree entry mode as the canonical listing type:

| mode | type |
| --- | --- |
| `040000` | `tree` |
| `100644` | `blob` |
| `100755` | `blob` |
| `120000` | `blob` |
| `160000` | `commit` |

This makes gitlinks visible as commit entries rather than incorrectly treating every non-directory object as a blob.

Unknown modes, malformed object IDs, and unsafe tree entry names fail rather than being emitted as trusted paths.

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
- `--format=FORMAT` — format records with `%(objectmode)`, `%(objecttype)`, `%(objectname)`, and `%(path)`;
- `--abbrev[=N]` — print unique object prefixes, default minimum 12 when no `N` is supplied;
- `-z` — terminate records with NUL instead of newline.

The default record is:

```text
<mode> <type> <64-hex-object-id><TAB><path>
```

## Recursive semantics

Without `-r`, direct children are reported. A nested literal pathspec such as `sub/file.txt` performs only the minimum subtree traversal necessary to reach that path.

With `-r`, file/gitlink entries are emitted recursively and tree entries are hidden unless `-t` or `-d` is requested. `-d -r` therefore provides a recursive tree-directory listing.

Gitlinks (`160000`) are leaves and are never traversed as subtrees.

## Pathspec behavior

Pathspecs are repository-tree-relative and may be provided positionally after `TREE-ISH` or after `--`.

Literal directory pathspecs select that directory and, when recursive, its descendants. Shell-style `fnmatch` patterns are supported. Traversal is pruned before opening subtrees that cannot contain a literal/static-prefix match, so selecting `src/...` does not force unrelated malformed subtrees to be read.

Absolute paths, backslashes, NULs, empty components, `.` components, and `..` components are rejected.

This is an intentionally compact educational pathspec subset; it does not claim native Git's complete pathspec magic syntax.

## Formatting

`format_ls_tree()` returns bytes so newline and NUL modes have one deterministic implementation.

Custom formats support:

- `%(objectmode)`
- `%(objecttype)`
- `%(objectname)`
- `%(path)`
- `%%` for a literal percent sign

Unknown atoms fail. `--name-only`, `--object-only`, and `--format` are mutually exclusive.

When `--abbrev` is requested, locally present objects use the shared uniqueness-aware abbreviation logic. A deliberately missing leaf object can still be displayed by prefixing its recorded OID; recursive traversal of a missing/wrong-type tree still fails.

## Safety and compatibility

- read-only: no ref, index, worktree, reflog, or object-store mutation;
- annotated-tag cycles are rejected;
- recursive tree cycles are rejected defensively;
- selected tree children must actually deserialize as trees before traversal;
- irrelevant subtrees can be skipped by pathspec pruning;
- the installed `pygit ls-tree` no longer depends on the legacy global argparse parser;
- the old `Repository.ls_tree()` remains available for compatibility.

## Regression coverage

Phase 75 covers:

- SHA-256 default records and all supported tree modes;
- regular, executable, symlink, subtree, and gitlink entries;
- recursive output, `-t`, and `-d` semantics;
- annotated tag, short SHA, direct tree, and `REV:path` resolution;
- packed-only abbreviated tree-ish resolution after repack;
- minimum nested literal pathspec traversal;
- glob selection and pathspec validation;
- pruning around unrelated malformed subtrees;
- default/name-only/object-only/custom/NUL/abbreviated formatting;
- invalid root, mode, and tree entry names;
- installed CLI routing through the modern front door.
