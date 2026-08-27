# Phase 156 — `ls-files` worktree selectors

Phase 156 extends `pygit ls-files` beyond index-only inspection with controlled worktree discovery. It adds the Git-style `--others` and `--ignored` selectors plus `--exclude-standard`, reusing pygit's existing ignore matcher instead of duplicating ignore semantics.

## Commands

```bash
pygit ls-files --others
pygit ls-files --others --exclude-standard
pygit ls-files --others --ignored --exclude-standard
pygit ls-files --cached --others
pygit ls-files --others --exclude-standard -z
```

`-o/--others` lists individual untracked files and symlinks. Paths already present in any index stage are excluded, as is `.pygit/` metadata. Existing path arguments continue to act as literal directory-prefix or shell-style wildcard filters.

`--exclude-standard` applies the repository's standard ignore stack already understood by pygit: `.pygitignore`, `.gitignore`, and `.pygit/info/exclude`. With ordinary `--others`, ignored paths are filtered out. Adding `-i/--ignored` inverts that selection and reports only ignored untracked paths.

For this phase, `--ignored` intentionally requires both `--others` and `--exclude-standard`; unsupported combinations fail explicitly instead of silently producing misleading output. `--exclude-standard` is likewise scoped to worktree discovery.

Index selectors remain composable. For example, `--cached --others` returns the union of tracked stage-0 paths and untracked worktree paths. NUL framing with `-z` applies to the combined output.

## Implementation boundary

Worktree scanning lives in `pygit.ls_files_others` rather than expanding the index-only `index_plumbing.ls_files()` contract. This keeps direct index APIs stable while providing a reusable worktree selector that can later support additional exclusion sources without coupling them to index serialization.

The scanner does not follow directory symlinks, but reports them as file-like candidates. Repository metadata is never traversed or emitted.
