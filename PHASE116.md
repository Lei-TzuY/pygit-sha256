# Phase 116 — cat-file follow-symlinks

Phase 116 adds Git-style in-tree symlink traversal to pygit's streaming `cat-file` batch modes without changing ordinary revision resolution.

## Commands

```bash
pygit cat-file --batch --follow-symlinks
pygit cat-file --batch-check --follow-symlinks
pygit cat-file --batch-command --follow-symlinks
pygit cat-file --batch-check --follow-symlinks -Z
```

`--follow-symlinks` is batch-only, matching Git. It is accepted with `--batch-all-objects`, where there are no `REV:path` records to traverse and therefore no behavioral change.

## In-tree traversal

For `REV:path` input, mode `120000` entries are read as symlink blobs and followed relative to the directory containing the link. Symlink chains may cross back toward the tree root through leading `../` components. A final link resolves to the object selected by its target, so normal batch metadata and optional raw contents describe the target object rather than the link blob.

Ordinary object expressions and non-symlink paths still use the existing object formatter and SHA-256 resolver. Other commands such as `rev-parse` retain their previous `REV:path` behavior; Phase 116 deliberately scopes symlink following to `cat-file`'s batch protocol.

## Special protocol records

Git does not format every symlink failure as `<expr> missing`. Phase 116 implements the dedicated records:

- `symlink <size>` followed by the path outside the tree when a link escapes the root or is absolute;
- `dangling <size>` followed by the original expression when a followed target does not exist;
- `loop <size>` followed by the original expression for cyclic/excessive symlink expansion;
- `notdir <size>` followed by the original expression when traversal continues through a non-directory.

A path that is simply absent before any symlink is followed keeps the canonical `<expr> missing` response. Custom batch formats apply only to successful object records; special records retain their fixed protocol shape.

## Framing and command mode

The same behavior composes with `--batch-command`, `--buffer`, custom batch formats, and Phase 93 NUL framing. Under `-Z`, both the special header and its following payload use NUL terminators, exactly like successful batch records use NUL framing.

## Safety

Traversal is object-database-only and never dereferences worktree filesystem links. Absolute targets and relative targets that climb above the selected tree are reported instead of opened. Cycles are detected from repeated expansion states with a bounded 40-hop fallback.

## Regression coverage

`tests/test_phase116.py` covers internal final links, nested relative links, raw batch contents, relative and absolute escapes, transformed outside paths with suffixes, dangling links, loops, non-directory traversal, ordinary missing paths, custom formats, batch-command info/contents, NUL framing, installed CLI validation/help, and `--batch-all-objects` composition.
