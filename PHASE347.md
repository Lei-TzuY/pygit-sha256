# Phase347: Guard FETCH_HEAD with final ref publication

Phase347 closes a cross-metadata concurrency gap in the mapped incremental protocol-v2 packfile-URI fetch path.

Before this phase, root certification completed and the populated durable `FETCH_HEAD` hook ran **before** the repository-wide publication guards were acquired. Two concurrent fetches could therefore both finish immutable staging/certification, then the losing transaction could overwrite `FETCH_HEAD` and only afterwards discover that it could not acquire the metadata guards or commit its tracking-ref CAS. The repository would remain object-safe, but `FETCH_HEAD` could describe transaction B while the tracking refs still reflected transaction A.

Phase347 changes the final mutable ordering to:

`download -> SHA-256 stage -> [durable immutable LMAP] -> certify -> publication guards -> state revalidation -> durable populated FETCH_HEAD -> tracking-ref CAS`

The expensive/network and immutable work remains outside the final guard critical section. Only the correlated mutable publication is serialized.

## Concurrency contract

After certification, a transaction must acquire the existing repository publication guards and prove that the bounded mutable state is unchanged from its preflight snapshot. Only then may it invoke the populated `FETCH_HEAD` hook. The hook remains before tracking-ref CAS, preserving the native-Git-compatible behavior established in Phase340: if ref publication itself subsequently fails, a complete certified fetched tip may remain in `FETCH_HEAD`.

The important distinction is that a transaction which **cannot enter the final publication critical section** is no longer allowed to alter populated `FETCH_HEAD` first.

If guard acquisition fails, `FETCH_HEAD` is not populated by that transaction. If state revalidation fails after guard acquisition, `FETCH_HEAD` is not populated. If durable `FETCH_HEAD` publication itself fails, the exception propagates, the guards are released, and refs are not published.

The initial post-discovery empty `FETCH_HEAD` replacement remains outside this final boundary because it represents native Git's stale-fetch-metadata clearing behavior, not a certified fetched-tip publication. Phase347 specifically correlates the populated certified result with the ref transaction.

## SHA-256-native invariants

Nothing in this phase changes identity domains:

- remote negotiation and compatibility identities are genuine full 40-hex SHA-1 values;
- local objects, refs, and `FETCH_HEAD` use genuine content-derived full 64-hex SHA-256 values;
- compatibility LMAP data contains only validated SHA-1 <-> SHA-256 mappings;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived object identity is introduced.

## Regression coverage

`tests/test_phase347.py` verifies:

- exact final ordering: guard acquisition -> state revalidation -> populated FETCH_HEAD -> ref CAS -> guard release;
- guard contention cannot run the populated FETCH_HEAD hook;
- state drift detected after guard acquisition aborts before populated FETCH_HEAD;
- FETCH_HEAD durability failure releases guards and prevents ref publication.

The inherited Phase340 tests continue to require native-compatible semantics where a ref-publication failure **after a successful certified FETCH_HEAD write** may leave the fetched tip recorded in `FETCH_HEAD`.

## Coordination

Phase347 is stacked directly on Phase346 exact-green head `01a88f583eb0b53317ea6e8df1c0a86f8fcd8975` / PR #323. Phase346 GitHub Actions Tests #2939 completed successfully before this branch was created. `main` was rechecked at `bfcbae64e4dc9997b915c16e1aa923a951090083`, and the `phase347` namespace was confirmed free immediately before branch creation.

This phase intentionally remains a stacked, open, unmerged pull request.
