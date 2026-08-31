# Phase348: Serialize incremental FETCH_HEAD state transitions

Phase348 closes the remaining cross-fetch race around Phase347's correlated populated `FETCH_HEAD` + tracking-ref publication window.

## Race closed

Phase347 correctly moved populated `FETCH_HEAD` publication inside the repository publication guards. However, a second named-remote fetch could still complete protocol-v2 discovery and perform the initial stale-`FETCH_HEAD` clear outside those guards.

That allowed this interleaving:

1. fetch A certifies its roots and enters the final Phase347 publication critical section;
2. A durably publishes its populated `FETCH_HEAD`;
3. before A finishes tracking-ref CAS, fetch B completes discovery and durably clears `FETCH_HEAD`;
4. B later loses publication-guard acquisition or state revalidation;
5. A's tracking refs remain the successful result while `FETCH_HEAD` is empty because of B.

The object graph and refs remain safe, but the two mutable metadata surfaces are no longer correlated.

## Native Git contract and pygit concurrency boundary

Git 2.55.0's `builtin/fetch.c` implements ordinary non-`--append` fetches by truncating `FETCH_HEAD` before later entries are opened/appended. Phase340 already adopted the visible stale-clear behavior and Phase344-346 strengthened it with crash-safe serialized replacement.

Git's ordinary single-fetch implementation does not provide a long transaction lock that serializes independent concurrent fetch processes from initial truncate through final ref publication. Phase348 therefore does **not** claim that `FETCH_HEAD.state.lock` is a native Git filename or wire contract. It is a pygit-internal concurrency guard required to preserve the stronger durable/correlated publication semantics established by Phases344-347.

## Implementation

The mapped incremental named-remote path now uses a dedicated:

`$GIT_DIR/FETCH_HEAD.state.lock`

The state guard is distinct from the existing durable writer's canonical `FETCH_HEAD.lock`:

- `FETCH_HEAD.lock` owns one atomic file replacement;
- `FETCH_HEAD.state.lock` correlates the two separate state transitions belonging to concurrent fetch lifecycles.

It is acquired with exclusive `O_CREAT|O_EXCL`, explicitly made non-inheritable, fsynced after its ownership marker is written, never stolen, and released only by the call that acquired it.

The early stale clear becomes:

`v2 discovery -> FETCH_HEAD state guard -> durable empty FETCH_HEAD -> release state guard`

The final publication becomes:

`download -> SHA-256 stage -> [durable immutable LMAP] -> certify -> FETCH_HEAD state guard -> repository publication guards -> state revalidation -> durable populated FETCH_HEAD -> tracking-ref CAS -> release repository guards -> release FETCH_HEAD state guard`

Network requests, external pack download, native-object staging, LMAP publication, and root certification remain outside the state guard. The phase therefore closes the metadata race without serializing expensive fetch work.

A competing early clear that arrives during another transaction's final publication window fails closed before touching `FETCH_HEAD`. A final transaction that cannot acquire repository publication guards releases the state guard and does not populate refs. Failure of the early durable empty replacement likewise releases the state guard.

## SHA-256-native invariants

Phase348 changes only metadata concurrency ordering:

- remote transport and compatibility identities remain genuine complete 40-hex SHA-1 values;
- local objects, tracking refs, and `FETCH_HEAD` remain genuine content-derived complete 64-hex SHA-256 values;
- LMAP remains validated SHA-1 <-> SHA-256 compatibility metadata;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived object identity is introduced.

## Regression coverage

`tests/test_phase348.py` covers:

- canonical dedicated state-guard path and fail-closed ownership;
- exact early clear ordering: state guard -> durable empty replacement -> release;
- exact final ordering: state guard -> repository guards -> state check -> populated FETCH_HEAD -> refs -> releases;
- a real populated durable FETCH_HEAD remaining intact when a racing clear is attempted inside the final publication window;
- release of the state guard when repository publication-guard acquisition fails;
- release of the state guard when the early durable clear fails;
- spawned-process contention proving a foreign process's state guard blocks the early clear without mutating the existing complete FETCH_HEAD.

Inherited Phase340-347 tests continue to cover repository-native SHA-256 formatting, stale replacement, durable writer lock ownership, multiprocess complete-file publication, LMAP durability, and the post-certification/ref-CAS ordering contract.

## Coordination

- actual `main` rechecked at `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase347 / PR #324 head `71622528232af9499e53a5d7b55ccfbda7893863`;
- Phase347 GitHub Actions Tests #2944: success;
- Python 3.9: 2593 passed;
- Python 3.13: 2593 passed;
- CI runner Git: 2.55.0;
- `phase348` namespace was collision-checked immediately before branch creation and was free.

This phase intentionally remains a stacked, open, unmerged pull request.
