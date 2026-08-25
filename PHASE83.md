# Phase 83 — reflog-aware `merge-base --fork-point`

Phase 83 completes the user-facing `merge-base` mode family with Git-style fork-point discovery for rebased or rewound upstream refs.

## CLI

```bash
pygit merge-base --fork-point upstream topic
pygit merge-base --fork-point upstream   # compares with HEAD
pygit merge-base --fork-point upstream 'topic@{0}'
```

The first argument must resolve to an actual ref. The optional second argument is a commit-ish and defaults to `HEAD`. The derived commit uses the shared Phase 57/78 revision resolver, so ancestry expressions, annotated tags, numeric reflog selectors, and packed-only objects compose without a fork-point-specific revision grammar. A successful query prints one SHA-256 commit ID and exits 0. If no retained ref tip is a valid fork point, the command prints nothing and exits 1.

`--fork-point` is mutually exclusive with `--is-ancestor`, `--octopus`, and `--independent`, and it cannot be combined with `--all`.

## Semantics

Ordinary `merge-base upstream topic` can move behind the place where `topic` was actually created after `upstream` is force-updated. Fork-point therefore considers the current resolved tip of the supplied ref plus every retained reflog `new_oid` for that ref.

Those tips are peeled to commits and treated as the parents of a hypothetical merge. The implementation computes best common ancestors between that hypothetical history and the requested commit using the existing Phase 52 graph plumbing.

A result is accepted only when there is exactly one best base **and that base is one of the current/historical ref tips**. An `old_oid` that is merely mentioned by the oldest retained reflog record is not promoted into an additional candidate: once a historical tip no longer appears as a retained reflog value, it is considered expired. Therefore if the relevant old tip has fallen out of the reflog, an older ordinary common ancestor is deliberately rejected instead of being returned as a misleading fallback.

## Safety and composition

Fork-point reuses the Phase 77 strict reflog reader, so malformed existing reflog records fail closed. Missing reflog files are allowed: the current ref tip remains a candidate, which lets a branch created directly from the current upstream tip still resolve normally.

Reflogs can outlive individual objects after pruning. A retained `new_oid` whose object is already unavailable is ignored as lost evidence; if that tip was necessary, no fork point is returned. An existing historical object that is not a commit after tag peeling is treated as repository inconsistency and fails loudly.

All ancestry traversal obeys `.pygit/shallow` boundaries. Candidate graph inputs are normalized to full SHA-256 commit IDs, while the public derived revision first goes through the modern shared resolver. The operation is fully read-only: no refs, reflogs, objects, index entries, or worktree files are changed.

## Python API

```python
from pygit import fork_point

point = fork_point(repo, "origin/main", "topic@{0}")
if point is None:
    print("no retained fork point")
else:
    print(point)
```

## Regression coverage

`tests/test_phase83.py` covers discarded upstream incarnations, default-`HEAD` behavior, missing reflogs, expiry of the exact historical tip while its OID remains only in a retained record's old-value field, forks from non-tip ancestors, exact-ref requirements, malformed reflog failure, installed CLI routing/help, mode validation, exit status, and preservation of the pre-existing merge-base modes.

`tests/test_phase83_hardening.py` adds shared numeric-reflog selector composition, packed-only discovery after repack, already-pruned unrelated historical tips, and fail-loud handling for existing non-commit historical tips.
