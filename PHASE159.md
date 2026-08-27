# Phase 159 — `ls-files --full-name` and subdirectory semantics

Phase 159 makes `pygit ls-files` behave like Git when invoked below the repository root.

```bash
cd subdir
pygit ls-files
pygit ls-files --full-name
pygit ls-files ../root.txt
pygit ls-files --others --directory
```

Without `--full-name`, `ls-files` now scopes an implicit query to the current directory and renders matching paths relative to that directory. For example, running from `sub/` reports `nested/file.txt` instead of `sub/nested/file.txt`, and an explicit `../root.txt` pathspec is preserved as `../root.txt` in output.

`--full-name` keeps the same selection scope but renders repository-root-relative names, matching Git's distinction between selection and display. Stage/unmerged records rewrite only the path field after the tab, and `-z` retains NUL framing after path conversion.

Worktree selectors use the same semantics. In particular, `ls-files --others --directory` from a wholly untracked current directory reports `./`, while `--full-name` reports the repository-relative directory such as `scratch/`.

CLI pathspecs are translated from the caller's current directory into the repository-root-relative form used internally. Pathspecs that would escape the repository are rejected instead of silently probing outside the worktree.
