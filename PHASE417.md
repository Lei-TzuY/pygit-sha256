# Phase 417 — branch copy from previous checkout selectors

Phase417 adds Git-compatible focused handling for copying a branch named by
previous-checkout syntax:

```text
pygit branch -c @{-N} <new>
pygit branch --copy @{-N} <new>
pygit branch -C @{-N} <new>
pygit branch --copy --force @{-N} <new>
pygit branch --force --copy @{-N} <new>
```

The branch-copy operation is intentionally different from ordinary branch
creation from a revision. `@{-N}` must expand to an existing local branch. If the
previous checkout was detached, the operation fails even though the detached
commit is otherwise a valid revision.

## Native Git behavior

Local SHA-256 Git 2.47.3 probes established:

- `branch -c @{-1} copy` copies the previous branch tip and leaves HEAD unchanged;
- `--copy` is equivalent to `-c`;
- `-C` and `--copy --force` may replace an existing non-current destination;
- replacing the currently checked-out destination is rejected;
- a detached previous checkout is rejected with `no branch named '@{-1}'`;
- literal `-` is not previous-checkout shorthand for branch copy;
- the destination reflog contains the complete source reflog followed by
  `Branch: copied refs/heads/<source> to refs/heads/<destination>`;
- source `branch.<source>.*` configuration is copied;
- when `-C` targets a branch with pre-existing config, native Git retains those
  destination values as effective duplicate-key overrides while adding
  source-only keys;
- copying a branch onto itself succeeds and appends the copy reflog event.

Phase417 mirrors those observable semantics within pygit's single-valued config
backend: existing destination keys are retained while source-only keys are
copied.

## Implementation

`pygit.branch_copy_previous_cli` owns only the exact copy forms above. It:

1. expands `@{-N}` through the established HEAD-reflog-backed resolver;
2. requires the expansion to name an existing local branch;
3. validates the destination as a `refs/heads/...` ref;
4. enforces ordinary/force destination safety;
5. writes the copied genuine local SHA-256 branch tip;
6. replaces the destination branch reflog with the source reflog and appends the
   forced same-OID native-style copy event;
7. copies arbitrary `branch.<source>.*` configuration keys without overriding
   pre-existing destination keys;
8. leaves HEAD, worktree, index, objects, and unrelated refs untouched.

The top-level application router intercepts only these exact previous-selector
copy shapes. Ordinary branch copy/move syntax, literal `-`, ordinary revisions,
and extra-argument forms remain legacy-owned.

## SHA-256-native invariants

This phase changes only local ref/config/reflog metadata.

- copied branch tips remain genuine content-derived 64-hex local SHA-256 OIDs;
- no remote/native 40-hex SHA-1 is synthesized, padded, truncated, or translated;
- selector text is never hashed into an identity;
- no object, packfile, FETCH_HEAD, object-map, promisor, shallow, or protocol
  state is created or modified;
- HEAD/worktree/index remain unchanged.

## Coordination

- actual main at phase start remained `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base is Phase414 / PR #376 head
  `1a43f5383577cdc64ba4d4dac0aeb0febac9e72f`;
- Phase414 Tests #3323 / run `33544930739` was success before Phase417 work;
- Phase415 was reserved independently for branch-move previous-selector work;
- an attempted Phase416 allocation was abandoned immediately after another
  worker created `phase416-rev-parse-previous-selector`;
- Phase417 was collision-checked and created from the exact-green Phase414 head.

The PR is intentionally stacked, open, and must not be auto-merged.
