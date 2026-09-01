# Phase410 — detached previous-checkout selectors

Phase410 extends the exact-green Phase409 checkout-history stack with focused
Git-compatible `checkout --detach @{-N}` handling.

## Behavior

`pygit checkout --detach @{-N}` first expands the selector from HEAD checkout
reflog history. If the expansion names a local branch, pygit resolves that
branch to its genuine local SHA-256 commit tip and checks out the commit ID so
HEAD remains detached. If the expansion is already a detached object ID, that
OID is used directly.

The application router deliberately intercepts only the exact
`checkout --detach @{-N}` shape. Ordinary detach checkout, path checkout, branch
creation, and other checkout grammar remain owned by the mature legacy parser.
Phase409's `checkout -` behavior is unchanged; the subtle native
`checkout --detach -` reflog spelling is intentionally deferred rather than
approximated.

## Native Git comparison

Git's checkout documentation defines `--detach` as pointing HEAD directly at
the selected commit rather than at a branch, and documents `@{-N}` as the N-th
last branch/commit checked out. The focused regression creates native Git and
pygit SHA-256 repositories, performs `main -> topic -> main`, then runs
`checkout --detach @{-1}` and compares detached HEAD plus the latest HEAD
reflog subject.

## SHA-256-native invariant

No object serialization, hashing, packfile, FETCH_HEAD, native-map, protocol,
or storage-format behavior changes. A branch selected for detachment is
resolved to its real local content-derived 64-hex SHA-256 commit ID. No SHA-1
padding/truncation, textual-ID rehashing, surrogate SHA-256, or metadata-derived
identity is introduced.

## Coordination

- latest main at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact base: Phase409 corrected head `f35819c384e95ff50cef16e2af13c7bddb904622`
- Phase409 Tests #3265 / run `33508999902`: success
- Phase410 namespace was collision-checked before branch creation
- no independent `switch` porcelain existed on current main, so this phase kept
  the scope inside the established checkout stack
