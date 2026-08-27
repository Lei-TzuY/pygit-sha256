# Phase 150 — status unmerged conflict classification

Phase 150 makes the persistent multi-stage index authoritative for `status`
conflict presentation.

High-level merge, cherry-pick, and rebase conflicts already populate stages 1
(base), 2 (ours), and 3 (theirs). Before this phase, the legacy status renderer
still treated every sidecar conflict as `both modified`, and porcelain output
could omit the conflict entirely or report the same path again as staged,
unstaged, or untracked.

## Git-style XY codes

`pygit status -s`, `pygit status --porcelain`, and
`pygit status --porcelain=v1` now classify unmerged paths from stage presence:

| stages | code | meaning |
| --- | --- | --- |
| 1 | `DD` | both deleted |
| 2 | `AU` | added by us |
| 1,2 | `UD` | deleted by them |
| 3 | `UA` | added by them |
| 1,3 | `DU` | deleted by us |
| 2,3 | `AA` | both added |
| 1,2,3 | `UU` | both modified |

The mapping is derived from the index itself rather than the older conflict
sidecar. A conflict path overrides ordinary staged/unstaged/untracked
classification so each tracked path produces only one short/porcelain record.

## Full status

Long-form `pygit status` now uses the matching human-readable labels under
`Unmerged paths:` instead of rendering every conflict as `both modified`.
For example, a modify/delete conflict where the incoming side deleted the file
is rendered as:

```text
Unmerged paths:
        deleted by them:        path.txt
```

## Porcelain and ignored paths

The modern status command accepts:

```console
pygit status -s
pygit status -sb
pygit status --porcelain
pygit status --porcelain=v1
pygit status --porcelain --ignored
```

Ignored paths are no longer emitted merely because `--porcelain` was selected;
`!!` records require `--ignored`, matching the script-facing contract expected
from Git porcelain v1.

## Branch header correction

The legacy renderer stored upstream information as a nested dictionary but read
`ahead` and `behind` from the outer status result. Phase 150 reads the nested
values correctly, so a branch one commit ahead renders:

```text
## main...origin/main [ahead 1]
```

instead of leaking the dictionary representation or silently dropping counts.

## Python helpers

`pygit.status_cli` exposes focused helpers for tests and integrations:

- `unmerged_status(repo)` returns `UnmergedStatus(path, code, stages)` values;
- `status_records(repo, ignored=False)` returns sorted porcelain-v1
  `StatusRecord(path, code)` values.

The historical `Repository.status()` dictionary is intentionally left
backward-compatible. Phase 150 normalizes it at presentation time rather than
changing callers that already depend on the older staged/unstaged lists.

## Regression coverage

`tests/test_phase150.py` covers:

- every one of the seven legal stage-presence combinations;
- a real three-way merge producing `UU` exactly once;
- a real modify/delete merge producing `UD` / `deleted by them`;
- `--porcelain=v1` grammar;
- full-status human labels;
- `git add`-style conflict resolution clearing the unmerged code;
- ignored-file opt-in behavior;
- `status -sb` nested upstream ahead/behind rendering.

## Scope boundary

This phase implements porcelain v1/short conflict classification. Porcelain v2,
rename-aware status records, sparse-index extensions, and submodule XY detail
remain separate work.
