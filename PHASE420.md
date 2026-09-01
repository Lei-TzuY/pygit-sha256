# Phase 420 — `ls-files --deduplicate`

Phase 420 restores Git's selector-origin duplicate semantics for filename-only `ls-files` output and adds the explicit `--deduplicate` switch.

## Why this matters

Git's `ls-files` selectors are additive. A path may therefore appear more than once when it is selected for multiple reasons. For example, a dirty tracked path selected by both `--cached` and `--modified` is printed twice. Likewise, a conflicted path stored in index stages 1, 2, and 3 appears three times in ordinary cached filename output.

Earlier pygit plumbing correctly retained one cached filename per stored conflict stage, but the modern CLI later applied an unconditional global de-duplication step. That silently erased useful selector/stage multiplicity and made `--deduplicate` impossible to express.

## Behavior

```bash
pygit ls-files --cached --modified
pygit ls-files --cached --modified --deduplicate
pygit ls-files --deduplicate conflict.txt
pygit ls-files --stage --deduplicate conflict.txt
pygit ls-files -z --cached --modified --deduplicate
```

- filename-only selectors preserve duplicate records by default;
- `--deduplicate` suppresses repeated filename records while preserving first occurrence order;
- plain cached output again preserves one filename per stored index stage;
- `--stage` and `--unmerged` retain all stage records and ignore `--deduplicate`, matching Git's documented rule;
- `-z`, path filtering, subdirectory display rewriting, and `--error-unmatch` continue to operate on the selected record stream.

The implementation keeps the existing `index_plumbing.ls_files()` API stable. The CLI invokes additive filename-only selectors independently, restores pathname ordering, and applies de-duplication only when explicitly requested. This avoids changing low-level callers that rely on the established Python helper contract.

## Compatibility reference

Current Git documents `--deduplicate` as suppressing duplicate filenames caused by multiple stages or multiple active selection statuses. It explicitly has no effect when `-t`, `--unmerged`, or `--stage` is active.

## Safety

This phase is read-only. It changes only `ls-files` selection/presentation and does not mutate the index, worktree, refs, reflogs, object database, packfiles, or repository format.
