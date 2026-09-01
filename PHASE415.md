# Phase415: move branches selected by previous checkout history

Phase415 extends the exact-green previous-checkout branch porcelain with Git-compatible branch renames:

```text
pygit branch -m @{-N} <new-branch>
pygit branch --move @{-N} <new-branch>
```

## Semantics

The `@{-N}` operand is expanded from HEAD checkout reflog history before mutation. The expansion must name a real local branch. A detached previous destination is a valid revision, but it is not a branch operand for `git branch -m`, so Phase415 rejects it before creating the destination ref.

For a successful move, pygit:

- preserves the genuine local content-derived 64-hex SHA-256 branch tip;
- creates the destination branch without checking it out when the source is not current;
- updates symbolic HEAD when the selected older checkout is the currently checked-out branch (for example `@{-2}` after `main -> topic -> main`);
- preserves the source branch reflog history and appends Git's same-OID rename event;
- moves pygit's flattened `branch.<name>.*` config keys to the destination branch name;
- leaves the worktree and index untouched;
- emits no success output;
- rejects an already-existing destination branch.

The reflog subject follows native Git exactly:

```text
Branch: renamed refs/heads/<old> to refs/heads/<new>
```

## Native Git differential

A SHA-256 native Git regression verifies `main -> topic -> main; git branch -m @{-1} renamed` and checks that:

- `renamed` points at the previous `topic` commit;
- `topic` no longer exists;
- HEAD remains on `main`;
- stdout is empty;
- `branch.topic.remote` / `branch.topic.merge` move under `branch.renamed.*` while the merge target value itself remains unchanged;
- the destination reflog ends with the native rename subject.

A focused pygit regression also verifies `branch --move @{-2} primary`, where the selector resolves the currently checked-out `main`; HEAD then becomes `ref: refs/heads/primary` and receives the same rename reflog event.

## Scope

Phase415 intentionally implements non-forced `-m` / `--move` only. Git's `-M` forced destination replacement has different safety and overwrite semantics and remains a later phase.

The generic legacy branch parser is unchanged. Only the exact previous-checkout move shapes are routed to the focused adapter.

## SHA-256-native boundary

No object serialization, hashing, packfiles, transport protocol, FETCH_HEAD, promisor state, object-map format, or storage format changes. `@{-N}` is selector metadata only; it is never padded, truncated, rehashed, or substituted for object identity. Local branch tips remain genuine 64-hex SHA-256 object IDs.

## Coordination

- exact base: Phase414 / PR #376 head `1a43f5383577cdc64ba4d4dac0aeb0febac9e72f`;
- Phase414 authoritative Tests #3323 / run `33544930739`: success;
- Python 3.9 / 3.13 on that base: 1377 passed each;
- CI Git on that base: 2.55.0;
- `phase415` namespace was collision-checked immediately before branch creation;
- independent protocol, bundle, init, and durability work is intentionally untouched.

This phase intentionally remains a stacked, open, unmerged pull request.
