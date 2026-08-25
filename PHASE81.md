# Phase 81: Reflog-aware merge-base fork points

Phase 81 adds `pygit merge-base --fork-point`, using the strict reflog reader from Phases 72/77 to recover a branch point after the reference side has been rewritten.

## CLI

```bash
pygit merge-base --fork-point upstream
pygit merge-base --fork-point upstream topic
pygit merge-base --fork-point refs/remotes/origin/main HEAD
```

The optional derived commit defaults to `HEAD`.  It is resolved through the shared revision resolver, so expressions such as `HEAD@{0}`, ancestry operators, annotated tags, and packed-only objects are available without a fork-point-specific revision parser.

A unique fork point is printed and the command exits 0.  If the available reflog cannot establish one unique fork point, the command is silent and exits 1.  Malformed refs, objects, or reflogs are errors and also exit non-zero.

`--fork-point` is incompatible with `--all` and with the other mutually exclusive merge-base modes.

## Why ordinary merge-base is insufficient

Suppose an upstream ref originally advanced through:

```text
A -- B0 -- B1 -- B2
      \
       T1        topic
```

and is later rewritten to another line:

```text
A -- C1 -- C2    upstream (current)
```

The ordinary merge base of current `upstream` and `topic` is only `A`.  The upstream reflog can still prove that `B0` was a historical upstream tip and an ancestor of the topic.  `--fork-point` therefore returns `B0` while that recovery information is still available.

## Candidate selection

The implementation:

1. requires `REF` to be a currently existing ref and requires a reflog for it;
2. resolves the derived commit through the shared SHA-256 revision resolver;
3. collects the current ref tip plus the reflog's historical old/new object IDs;
4. ignores zero-OID deletion sentinels and historical OIDs whose objects have already been pruned;
5. requires every surviving historical object to peel to a commit;
6. keeps only candidates reachable from the derived commit, honoring `.pygit/shallow` boundaries;
7. removes an eligible candidate when it is an ancestor of a newer eligible candidate; and
8. returns a result only when exactly one undominated candidate remains.

This graph-based reduction is intentionally stronger than selecting the first reflog entry that happens to be an ancestor.  It handles ref rewinds and refuses to guess when two incomparable historical ref tips are both ancestors of a merge-shaped derived history.

## Safety and failure semantics

Reflog parsing reuses the Phase 72/77 strict path and record validators.  Malformed records, unsafe paths, symlink escapes, or existing non-commit historical objects fail loudly rather than being treated as absent history.

Missing historical objects are different: reflogs may outlive objects after expiry/pruning, so unavailable historical OIDs are ignored.  If the missing object was necessary to prove the fork point, the result naturally becomes unavailable and the command exits 1.

The operation is fully read-only.  It does not update refs, reflogs, objects, the index, or the worktree, and it remains valid when the relevant commit graph is stored only in pack files.

## Python API

```python
from pygit import fork_point

base = fork_point(repo, "upstream", "topic")
if base is None:
    print("no unique fork point")
```

## Scope boundary

Phase 81 does not synthesize reflog entries, infer upstream configuration, rewrite refs, or approximate a fork point when reflog evidence is missing or ambiguous.  It also leaves the existing default, `--all`, `--is-ancestor`, `--octopus`, and `--independent` merge-base modes unchanged.
