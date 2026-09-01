# Phase 406 — native checkout reflog messages

Phase406 upgrades newly-written HEAD checkout reflog records from pygit's historical `checkout: moving to Y` spelling to native Git's `checkout: moving from X to Y` form.

## Behavior

The conversion is centralized at the `RefStore` HEAD-update boundary. Existing callers can continue passing the historical `moving to` message while new reflog records capture the pre-mutation HEAD source and persist the native shape.

- symbolic HEAD sources are recorded as the short local branch name;
- detached HEAD sources are recorded as the genuine full local 64-hex SHA-256 object ID;
- unrelated messages such as rebase, merge, clone, orphan, bisect, and ordinary ref updates are left byte-for-byte unchanged;
- historical repositories containing old `checkout: moving to Y` records are not rewritten and remain readable through Phase405's compatibility parser.

This also upgrades branch-creation paths that already switch HEAD through `set_head_symbolic(..., message="checkout: moving to ...")` without duplicating source-selection logic across porcelain callers.

## Native Git differential

Native Git records ordinary branch switching as `checkout: moving from <old> to <new>`. A focused regression creates a native SHA-256 repository, performs `main -> topic -> main`, reads `git reflog -1 --format=%gs HEAD`, and compares that message with pygit's result. A local probe also confirms `git checkout -b topic` records `checkout: moving from main to topic`.

Phase405's `check-ref-format --branch @{-N}` resolver now consumes these authoritative new records directly, while retaining conservative fallback support for old pygit reflogs.

## SHA-256-native invariants

No object or reference identity algorithm changes in this phase. Detached checkout sources are recorded from the real local HEAD identity and remain full 64-hex SHA-256 values. Remote/native interoperability elsewhere continues to use genuine complete 40-hex SHA-1 identities where Git requires them. No padding, truncation, textual-ID rehashing, surrogate SHA-256, or metadata-derived object identity is introduced.

## Coordination

- exact base: Phase405 compatibility-fix head `c12cf5b949c200e66714965e7177dd5a4c6e40ca`;
- Phase405 Tests #3240 completed successfully before Phase406 implementation;
- `phase406` namespace was collision-checked and free;
- active clone, init, protocol-v2, and loose-object durability stacks remain untouched.

This phase is intentionally delivered as an open, unmerged pull request.
