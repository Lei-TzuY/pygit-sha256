# Phase421: fail-closed previous-selector branch copy

Phase421 hardens the Phase417 branch-copy implementation after Phase419 integrated the previous-selector porcelain siblings.

The supported Git-visible syntax does not change. The phase only upgrades the local mutation boundary for:

- `branch -c/--copy @{-N} <new>`;
- `branch -C @{-N} <new>`;
- the existing explicit copy + force forms.

## Problem

Phase417 correctly validates the source selector, destination, checked-out destination safety, native-style reflog history, and branch configuration semantics. Its mutation sequence was still multi-step, however:

1. update/create the destination ref;
2. replace/append the destination reflog;
3. copy source branch configuration.

An I/O or configuration failure after step 1 could therefore leave a partially completed copy. The risk is most visible for `-C`: an existing destination may already have had its tip and reflog replaced before a later config write fails.

Phase415 already established an exact-file snapshot/restore model for previous-selector branch moves. Phase421 reuses that proven boundary for branch copy rather than inventing a second rollback representation.

## Atomic mutation boundary

After all ordinary Phase417 validation succeeds, Phase421 snapshots the exact bytes/existence of every file the copy may mutate:

- `.pygit/config`;
- `.pygit/packed-refs`;
- the destination loose branch ref;
- the destination branch reflog.

The existing successful copy steps then run unchanged. If any ref, reflog, or config step raises, those paths are restored to their exact pre-copy bytes/existence and the original exception is re-raised.

Source branch state, HEAD, HEAD reflog, worktree, index, and objects are not mutation targets of branch copy and remain outside this snapshot set.

## Regression coverage

`tests/test_phase421.py` injects failures at both early and late points:

- a forced copy onto an existing destination completes the ref/reflog/config mutation and then raises; the destination tip, reflog, config, and packed-ref state must be byte-for-byte restored;
- copying to a new destination and then failing leaves no destination ref, reflog, or branch config;
- an injected copy reflog failure after destination ref materialization restores the old destination ref/reflog;
- the ordinary successful copy path still retains Phase417 tip/config/reflog semantics.

Inherited Phase417 and Phase419 native SHA-256 differentials remain authoritative for successful Git-visible behavior.

## SHA-256-native invariants

Phase421 changes only failure atomicity for local branch metadata. Branch tips remain genuine content-derived full 64-hex SHA-256 OIDs. No remote/native SHA-1 identity, object serialization, packfile, transport, promisor, object-map, FETCH_HEAD, shallow state, index, or worktree behavior is changed.

## Coordination

- exact base: Phase419 / PR #381 head `d52367cc5ed499e12de0e48b955d4116bf04a77e`;
- Phase419 Tests #3374 / run `33554494081`: success, 1410 passed on Python 3.9 and 1410 passed on Python 3.13, Git 2.55.0;
- Phase420 is an independent `ls-files` deduplication line and is intentionally not duplicated;
- Phase421 namespace was collision-checked immediately before creation.

This phase intentionally remains a stacked, open, unmerged pull request.
