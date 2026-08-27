# Phase 157 — `ls-files --killed`

Phase 157 adds Git-style `ls-files -k/--killed` worktree obstruction reporting.

A killed path is an untracked file or symlink that blocks checkout of an indexed path because the filesystem has a file/directory conflict. Both conflict directions are covered: an untracked file can occupy a directory needed by a tracked descendant, and an indexed file pathname can currently be a directory containing untracked files.

```bash
pygit ls-files --killed
pygit ls-files --cached --killed
pygit ls-files --killed -- path
pygit ls-files --killed -z
```

Killed-path discovery is independent of ignore rules, matching Git's purpose as an obstruction diagnostic. `.pygit/` metadata is never traversed. Path filters apply to the emitted obstruction path, and `-z` preserves NUL framing when killed records are combined with index selectors.

The implementation lives in `pygit.ls_files_killed`, separate from JSON index plumbing and from ordinary `--others` ignore selection.