# Phase 326 — lock packfile-URI publication state

Phase326 hardens the exact-green Phase325 repository-level packfile-URI transaction by closing the remaining check-to-publication race for mutable repository-wide metadata.

## Why this phase exists

Phase325 snapshots `HEAD`, `packed-refs`, promisor metadata, shallow metadata, target refs, and relevant reflogs before external-pack download/import work and compares those bytes immediately before ref publication. That catches any writer which changes mutable state during the long network/import window.

A snapshot comparison by itself still leaves a very small time-of-check/time-of-use interval between the final comparison and Phase323's target-ref transaction. Phase323 already solves this for the actual target refs with canonical `<ref>.lock` files plus expected-old compare-and-swap. Phase326 now protects the repository-wide metadata surfaces which are not covered by those target-ref locks.

## Final publication boundary

After external packs are downloaded and verified, the complete native object graph is imported through the isolated SHA-256 staging boundary, and requested roots are certified, Phase326 acquires:

- `HEAD.lock`
- `packed-refs.lock`
- `shallow.lock`
- `promisor.json.lock`

Only after those locks are held does the transaction repeat the exact Phase325 byte/existence comparison and then call Phase323 ref publication. The locks stay held until the per-ref CAS transaction succeeds or fails.

The expensive network/download/import work happens before these locks are acquired, so normal writers are not blocked while external packs are being transferred.

Target `<ref>.lock` files are deliberately not duplicated here. Phase323 remains the authority for target-ref locks, expected-old SHA-256 CAS, object-type validation, reflog publication, and rollback.

## Failure semantics

Lock acquisition uses exclusive creation and never steals or overwrites another writer's lock. If any required metadata lock already exists, the transaction aborts before ref publication, removes only locks it created itself, and preserves the pre-existing lock byte-for-byte.

If mutable state changed before the guard locks were acquired, the Phase325 snapshot comparison still catches that change after lock acquisition and aborts before publication. If Phase323 later fails because of CAS, target-ref lock contention, object validation, or I/O, Phase326 releases its metadata locks in `finally`.

As in Phase321–325, successfully imported immutable content-addressed objects may remain unreachable after a later failure. No failed transaction is represented as a successful ref publication.

## Git compatibility

The locking model follows the files-backend convention used by Git for mutable repository state: lockfiles are created adjacent to the file being updated and writers must not steal an existing lock. Phase323 already applies the same convention to target refs. `promisor.json.lock` is pygit's corresponding lock namespace for its own persistent promisor metadata.

This phase does not change protocol-v2 wire behavior or remote object identity.

## SHA-256-native invariants

- remote object roots remain genuine full 40-hex SHA-1 identities;
- local object/ref identities remain full content-derived 64-hex SHA-256 values;
- no SHA-1 padding, truncation, translation, surrogate SHA-256, or metadata-derived local identity is introduced;
- publication locks contain no object identity and cannot materialize objects;
- refs remain the final mutable commit point.

## Coordination

- `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact base: Phase325 / PR #301 head `57b4c7ba7306f3cf0c5c5da101c326d19c76f183`
- Phase325 authoritative Tests #2788: success
- `phase326` was collision-checked immediately before branch creation
- this phase intentionally remains stacked on the exact-green packfile-URI line and does not merge any sibling work

## Tests

`tests/test_phase326.py` covers:

- all four metadata locks being held during the Phase323 publication call;
- successful cleanup after publication;
- contention on every guard lock aborting before ref publication;
- preservation of a pre-existing writer's lock;
- cleanup after a final publication/CAS failure;
- mutation immediately before guard acquisition still being detected by the Phase325 snapshot;
- no duplicate acquisition of Phase323 target-ref locks.

The inherited full suite remains authoritative for native Git protocol-v2 behavior, packfile-URI negotiation/download/checksum verification, SHA-256-native import, root certification, and target-ref CAS publication.

This phase does not merge its PR automatically.
