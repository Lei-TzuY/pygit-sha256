# Phase 405 — `check-ref-format --branch @{-N}`

Phase405 extends the exact-green Phase404 reference-plumbing stack with Git's previous-checkout shorthand in branch mode.

## Behavior

`pygit check-ref-format --branch @{-N}` now consults the local HEAD reflog and expands the selector before ordinary branch-name validation. `N` is a positive decimal checkout index counted from the newest checkout operation. Leading zeroes are accepted (`@{-01}` is equivalent to `@{-1}`), while `@{-0}` and selectors beyond available checkout history fail closed.

The shorthand is intentionally limited to `--branch`, matching Git's documented synopsis. Ordinary `check-ref-format @{-1}` remains invalid.

## Reflog compatibility

Native Git records checkout reflogs as `checkout: moving from <old> to <new>`. Those records are authoritative when present.

Historical pygit checkout records used the narrower `checkout: moving to <new>` form. Phase405 keeps those repositories usable by reconstructing the source conservatively:

1. prefer the next older checkout destination when it still names the old HEAD OID;
2. otherwise use a unique local branch whose tip equals the old HEAD OID;
3. otherwise retain the old non-zero OID as the detached previous checkout;
4. fail rather than guess when no source can be established.

No reflog is rewritten by this feature.

## Native Git differential

The regression suite creates a native repository, switches `main -> topic -> main`, and verifies that native Git returns:

- `@{-1}` -> `topic`
- `@{-2}` -> `main`
- `@{-0}` -> failure
- an out-of-range selector -> failure

The same selector shape is covered against pygit's legacy checkout reflog format.

## SHA-256-native invariants

This phase only reads HEAD reflog metadata and validates the resulting branch token. It writes no object, ref, reflog, mapping, `FETCH_HEAD`, packfile, or promisor state. Local object identity remains genuine content-derived 64-hex SHA-256. Native/remote compatibility identities remain genuine complete 40-hex SHA-1 wherever Git interoperability requires them; no padding, truncation, textual-OID rehashing, surrogate identity, or metadata-derived SHA-256 is introduced.

## Coordination

- exact base: Phase404 head `e23a68d5a2ce7fa5f8e3ca532d01fc940eb2e772`
- Phase404 Tests #3232 completed successfully before Phase405 branch creation
- `phase405` namespace was collision-checked and free
- active clone, init, protocol-v2, and loose-object durability stacks were left untouched

This phase is intentionally delivered as an open, unmerged pull request.
