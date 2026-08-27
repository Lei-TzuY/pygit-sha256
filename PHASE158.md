# Phase 158 — `ls-files --directory`

Phase 158 adds Git-style untracked-directory collapsing for `ls-files --others`.

```bash
pygit ls-files --others --directory
pygit ls-files --others --directory --no-empty-directory
pygit ls-files --others --directory --exclude-standard
pygit ls-files --others --ignored --exclude-standard --directory
pygit ls-files --others --directory -- path
```

`--directory` reports a wholly-untracked directory tree as one trailing-slash record such as `vendor/` instead of enumerating every file beneath it. A directory containing any indexed descendant is not collapsed, so untracked files beside tracked content remain visible individually. Narrow path filters also prevent an ancestor directory from being collapsed when the requested path lies below it.

`--no-empty-directory` suppresses trees containing only empty directories while still treating symlinks as file-like entries. It is accepted only together with `--directory`.

Standard ignore selection composes with directory mode: ordinary `--others --exclude-standard` omits ignored directory trees, while `--ignored --exclude-standard --directory` can report an ignored directory as a single record. `.pygit/` metadata is never traversed or emitted.

The implementation remains in `pygit.ls_files_others`, preserving the separation between worktree discovery and JSON index plumbing.