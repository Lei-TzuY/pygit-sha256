# Phase 160 — selector-aware `ls-files --error-unmatch`

Phase 160 completes `pygit ls-files --error-unmatch` so pathspec validation is based on the records actually selected for output rather than merely on whether a path exists somewhere in the index.

## What changed

- `--error-unmatch` now works with `--others` and `--killed`; the previous parser restriction is removed.
- Selector-specific validation now rejects a tracked path that is not selected by `--deleted`, `--modified`, or another active selector.
- Mixed selectors such as `--cached --others` validate each pathspec against the union of all selected index and worktree records.
- Stage-formatted records, directory records, wildcard pathspecs, and subdirectory-relative path translation are validated using their repository-root paths before display rewriting.
- The existing `-z` and `--full-name` rendering stages remain unchanged because validation happens before output formatting.

## Examples

```text
pygit ls-files --deleted --error-unmatch tracked.txt
pygit ls-files --others --error-unmatch scratch.txt
pygit ls-files --cached --others --error-unmatch tracked.txt scratch.txt
pygit ls-files --killed --error-unmatch obstructing-path
```

A pathspec that selects no record raises an error instead of silently succeeding.

## Verification

`tests/test_phase160.py` covers:

1. selector-aware `--deleted` success and failure;
2. untracked `--others` validation;
3. mixed cached/untracked selector unions;
4. killed filesystem obstructions;
5. subdirectory-relative pathspec validation.

This closes the partial `--error-unmatch` implementation without changing pygit's readable JSON index format.
