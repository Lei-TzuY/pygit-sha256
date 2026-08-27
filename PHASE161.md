# Phase 161 — Explicit `ls-files` exclude sources

Phase 161 extends the worktree-facing `ls-files --others` implementation with explicit exclusion sources that can be composed with the standard ignore stack.

## Added behavior

- `-x PATTERN` / `--exclude=PATTERN` may be repeated to exclude matching untracked paths.
- `-X FILE` / `--exclude-from=FILE` may be repeated; blank lines and comment lines beginning with `#` are ignored.
- `--ignored` now accepts any configured exclusion source: `--exclude-standard`, `-x`, or `-X`.
- Slashless wildcard patterns such as `*.tmp` apply recursively to path components; directory patterns such as `build/` cover descendants.
- Explicit exclusions compose with `.gitignore`, `.pygitignore`, and `.pygit/info/exclude` when `--exclude-standard` is also present.
- `--directory` avoids collapsing a wholly-untracked directory when an explicit descendant exclusion would otherwise hide a mixed included/excluded tree. Directly excluded trees can still collapse under `--ignored --directory`.
- Exclusion options are rejected unless `--others` is active, and unreadable `-X` files fail with a clear CLI error.

## Verification

`tests/test_phase161.py` covers recursive `-x` filtering, `--ignored` with explicit patterns only, `-X` parsing, directory-collapse safety, directly excluded directory output, option validation, and unreadable pattern files.

The feature remains deliberately scoped to `ls-files --others`; index selectors continue to use pathspec filtering rather than worktree exclusion rules.
