# Phase410 — detached previous-checkout selectors

Phase410 extends the exact-green Phase409 checkout-history stack with focused
Git-compatible `checkout --detach @{-N}` handling.

## Behavior

`pygit checkout --detach @{-N}` first expands the selector from HEAD checkout
reflog history. If the expansion names a local branch, pygit detaches HEAD at
that branch's genuine local SHA-256 commit tip while preserving the expanded
symbolic branch name in the checkout reflog. If the expansion is already a
detached object ID, that OID is used directly as both destination identity and
reflog destination.

That distinction matches native Git: detaching `@{-1}` when it expands to
`topic` leaves HEAD pointing directly at `topic`'s commit, while the reflog
subject is `checkout: moving from <old> to topic`, not a rewritten raw object
ID.

The application router deliberately intercepts only the exact
`checkout --detach @{-N}` shape. Ordinary detach checkout, path checkout, branch
creation, and other checkout grammar remain owned by the mature legacy parser.
Phase409's `checkout -` behavior is unchanged; `checkout --detach -` remains
deferred rather than approximated.

## Native Git comparison

Git's checkout documentation defines `--detach` as pointing HEAD directly at
the selected commit rather than at a branch, and documents `@{-N}` as the N-th
last branch/commit checked out. The focused differential creates native Git and
pygit SHA-256 repositories, performs `main -> topic -> main`, then runs
`checkout --detach @{-1}`.

The comparison checks the semantics relevant to this operation instead of
assuming two independently-created repositories must have byte-identical commit
IDs: both implementations must end detached, each detached HEAD must equal its
own `topic` branch tip, both object IDs must be 64-hex SHA-256 identities, and
the newest HEAD reflog subject must match native Git.

## SHA-256-native invariant

No object serialization, hashing, packfile, FETCH_HEAD, native-map, protocol,
or storage-format behavior changes. A branch selected for detachment is
resolved to its real local content-derived 64-hex SHA-256 commit ID. The
symbolic reflog destination is presentation/history metadata only; it never
replaces or synthesizes object identity. No SHA-1 padding/truncation,
textual-ID rehashing, surrogate SHA-256, or metadata-derived identity is
introduced.

## Coordination

- latest main at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact base: Phase409 corrected head `f35819c384e95ff50cef16e2af13c7bddb904622`
- Phase409 Tests #3265 / run `33508999902`: success
- Phase410 namespace was collision-checked before branch creation
- initial Phase410 Tests #3273 exposed the native reflog spelling mismatch and
  an over-constrained cross-repository OID assertion; both are corrected on the
  updated Phase410 head before any Phase411 work is started
- no independent `switch` porcelain existed on current main, so this phase kept
  the scope inside the established checkout stack
