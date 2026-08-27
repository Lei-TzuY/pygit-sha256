# Phase 154 — status untracked-file modes

Phase 154 adds Git-style `status -u/--untracked-files` presentation without changing the legacy `Repository.status()` API, which continues to expose individual untracked filesystem paths to Python callers.

## Commands

```bash
pygit status
pygit status -uno
pygit status -u
pygit status -uall
pygit status --untracked-files=no
pygit status --untracked-files=normal
pygit status --untracked-files=all
pygit status --porcelain=v2 -uall
```

## Modes

Git's default status mode is `normal`:

- `no` suppresses untracked records entirely;
- `normal` reports root-level untracked files and collapses a directory containing no tracked/index paths to one `dir/` record;
- `all` reports each individual untracked file, including files below otherwise collapsible directories.

A bare `-u` or `--untracked-files` selects `all`, while omitting the option selects `normal`. Attached short spellings such as `-uno`, `-unormal`, and `-uall` are accepted by the command parser.

Directory collapsing is index-aware. If a directory contains a tracked or unresolved-stage path, status does not collapse that directory and instead descends far enough to report nearby untracked paths accurately. Pure untracked subdirectories beneath a mixed tracked directory can still collapse independently.

## Output formats

The same selected untracked records feed long status, short status, porcelain v1, porcelain v2, and NUL-framed porcelain output. This keeps format choice separate from path selection:

- porcelain v1 uses `?? path`;
- porcelain v2 uses `? path`;
- `-z` retains raw pathname/NUL framing from Phase 153;
- `--untracked-files=no` hides untracked paths but does not hide tracked staged, unstaged, or conflict records.

## Ignored paths

`--ignored` retains Git's traditional interaction with untracked modes: ignored directories are collapsed in the default/`no` modes, while `--untracked-files=all` displays individual ignored files inside those directories. The existing `!!` / `!` porcelain markers remain unchanged.

## Compatibility and safety

The implementation is presentation-only and read-only. It does not mutate the index, refs, object database, ignore files, or worktree. Existing callers of `Repository.status()` keep receiving the complete individual-path inventory; only the command-facing status layer groups or suppresses those paths.

Rename detection, configurable `status.showUntrackedFiles`, ignored `matching` mode, column output, submodule dirtiness policy, and pathspec-limited status remain separate future work.
