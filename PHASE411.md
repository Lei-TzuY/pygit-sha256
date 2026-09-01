# Phase411 — detached previous-checkout dash shorthand

Phase411 extends the exact-green Phase410 checkout-history stack with the exact
Git-compatible `checkout --detach -` form.

## Behavior

Git documents `-` as synonymous with `@{-1}` for checkout targets. Phase410
already taught the focused previous-checkout adapter how to detach at an
`@{-N}` expansion, and its CLI already normalizes a literal `-` selector to
`@{-1}` before calling the operation layer. Phase411 therefore keeps the
production change deliberately narrow: the top-level application router now
recognizes the exact three-token form `checkout --detach -` and sends it through
the same focused adapter.

The operation semantics are unchanged from Phase410:

- if `@{-1}` expands to a local branch, HEAD becomes detached at that branch's
  genuine local SHA-256 commit tip while the checkout reflog records the symbolic
  destination name;
- if the previous checkout was already detached, its genuine local SHA-256 OID
  is used directly;
- sparse-checkout restoration, index reconstruction, and the post-checkout hook
  continue through the Phase410 detached-checkout path;
- every other checkout grammar remains owned by the mature legacy parser.

The router intentionally requires the exact form. Extra arguments, ordinary
`checkout --detach <branch>`, branch creation, path checkout, and other checkout
options are not claimed by this adapter.

## Native Git comparison

Current Git 2.55 documents `-` as synonymous with `@{-1}` for checkout. A native
SHA-256 differential creates `main` and `topic`, performs `main -> topic -> main`,
then executes `git checkout --detach -`.

The expected observable behavior is:

- HEAD is detached;
- the detached HEAD equals the repository's own `topic` tip;
- the object ID is a 64-hex SHA-256 identity;
- the newest HEAD reflog subject is `checkout: moving from main to topic`.

Phase411's regression performs the same sequence through pygit and checks those
semantics without assuming independently-created repositories must have identical
commit IDs.

## SHA-256-native invariant

No object serialization, hashing, packfile, protocol, FETCH_HEAD, object-map, or
storage-format behavior changes. A previous branch is resolved to its genuine
content-derived local 64-hex SHA-256 commit ID before HEAD is detached. The `-`
token and reflog destination are selector/history syntax only; they never become
object identity and are never padded, truncated, or rehashed into surrogate IDs.

## Coordination

- latest main at phase start remains `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase410 replacement / PR #371 head
  `250b1e1a360f4c1de3b1e922aef5c3ec0725f910`;
- Phase410 replacement Tests #3282 / run `33527341955`: success;
- Python 3.9: 1356 passed;
- Python 3.13: 1356 passed;
- CI Git: 2.55.0;
- Phase411 namespace was collision-checked immediately before branch creation;
- this phase intentionally remains stacked, open, and unmerged.
