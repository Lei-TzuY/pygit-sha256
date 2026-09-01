# Phase412 — previous-checkout start points for branch creation

Phase412 extends the exact-green Phase411 checkout-history stack with Git-compatible
previous-checkout start points for new branch creation:

```text
pygit checkout -b <new> @{-N}
pygit checkout -b <new> -
```

## Behavior

The implementation is deliberately additive. A focused adapter handles only the
exact four-token `checkout -b <branch> <previous-selector>` shapes, where the
start point is either `@{-N}` or the one-character `-` shorthand for `@{-1}`.
Every ordinary branch/tag/SHA start point continues through the mature legacy
checkout parser unchanged.

The adapter reuses Phase408's HEAD-reflog-backed `expand_previous_checkout()`
resolver. After expansion it delegates to the existing branch creation and
checkout operations:

1. expand previous-checkout syntax before mutation;
2. create the new branch with `repo.branch(..., start_point=<expanded>)`;
3. switch to the new branch with `repo.checkout(...)`.

If the previous destination is symbolic, the existing branch name is supplied as
the start point. If it was detached, its genuine local 64-hex SHA-256 commit OID
is supplied directly. No selector text becomes object identity.

Missing or invalid previous-checkout history fails before the new branch is
created.

## Native Git comparison

Native SHA-256 Git accepts both `git checkout -b new @{-1}` and
`git checkout -b new -`. For `main -> topic -> main`, where `topic` has a commit
not on `main`, the new branch points at the topic commit, HEAD becomes symbolic
`new`, and the newest HEAD reflog subject is:

```text
checkout: moving from main to new
```

Phase412's differential checks the same semantics without requiring two
independently-created repositories to produce identical commit IDs.

## Router ownership

Only these exact forms are intercepted:

```text
checkout -b <new> @{-N}
checkout -b <new> -
```

Ordinary `checkout -b <new> main`, extra arguments, path checkout, detached
checkout, orphan checkout, and all other grammar remain legacy-owned.

## SHA-256-native invariant

No object serialization, hashing, packfile, protocol, FETCH_HEAD, object-map, or
storage-format behavior changes. A previous detached destination is retained as
its genuine content-derived local 64-hex SHA-256 commit ID. A symbolic previous
branch is resolved by the existing branch operation. No SHA-1 padding,
truncation, textual-ID rehashing, or surrogate identity is introduced.

## Coordination

- latest main at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase411 / PR #372 head
  `d1f90adddf3517c8724ce27fc931077fcc6e0abe`;
- Phase411 Tests #3291 / run `33532276116`: success;
- Python 3.9: 1360 passed;
- Python 3.13: 1360 passed;
- CI Git: 2.55.0;
- Phase412 namespace was collision-checked immediately before branch creation;
- the active checkout stack is used instead of older unrelated durability
  branches;
- this phase intentionally remains stacked, open, and unmerged.
