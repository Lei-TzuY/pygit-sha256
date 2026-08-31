# Phase357: Integrate publication guard ownership

Phase357 combines the two independently verified cleanup-ownership boundaries used by mapped incremental protocol-v2 packfile-URI fetches:

- Phase353: inode-aware ownership for repository-wide publication guards (`HEAD.lock`, `packed-refs.lock`, `promisor.json.lock`, `shallow.lock`);
- Phase356: inode-aware ownership for the separate `FETCH_HEAD.state.lock` lifecycle guard.

## Composition

The branch is based on Phase356's exact-green head. Phase353's production module, regression file, and phase note are copied byte-for-byte from Phase353's exact-green head rather than reimplemented.

The resulting final publication stack can therefore hold both ownership classes at once:

`FETCH_HEAD.state.lock -> repository publication guards -> state revalidation -> populated FETCH_HEAD -> tracking-ref publication`

The state lock protects the cross-fetch FETCH_HEAD lifecycle. Repository guards protect the bounded mutable repository publication surface. They remain distinct lock classes with distinct ownership tables and cleanup operations.

## Cross-layer invariant

A stale cleanup from either class must never intentionally delete a replacement lock that no longer names the acquired inode.

`tests/test_phase357.py` exercises the complete incremental transaction with both classes active. Inside the pre-ref publication hook it removes and recreates:

1. the active `FETCH_HEAD.state.lock` pathname; and
2. one active repository publication-guard pathname.

The recreated files use different content, but correctness is based on retained descriptor identity rather than bytes. After the transaction completes:

- both replacement pathnames remain present;
- all other repository guards owned by the transaction are removed normally;
- both ownership registries are empty;
- both retained descriptors are closed;
- the mocked ref publication result still completes, demonstrating that cleanup of one guard class does not consume ownership of the other.

A second regression explicitly releases the state guard first and verifies repository-guard ownership remains intact until its own release, then checks both registries are empty.

## Exact sibling reuse

The following Phase353 blobs are reused exactly:

- `pygit/protocol_v2_packfile_uri_transaction.py` — `79aee3438f858df722f5e328d3fd8502a732fc76`;
- `tests/test_phase353.py` — `f58f6c8549fbe61986dc700aa2c9127b1bdc7362`;
- `PHASE353.md` — `0efd9dc49f3f8f9bff2925a47469eb15bf68c374`.

Phase356 remains unchanged beneath this integration commit.

## SHA-256-native invariants

This phase only composes lock ownership boundaries. Remote/native compatibility identities remain genuine complete 40-hex SHA-1 values. Local objects, refs, and `FETCH_HEAD` remain genuine complete content-derived 64-hex SHA-256 values. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- actual `main` remains `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase356 / PR #332 head `5655f108b68e68f1c262a19ee7675c7294296597`;
- Phase356 Tests #3005: Python 3.9 / 3.13 both 2625 passed, Git 2.55.0;
- Phase353 / PR #330 head `d2da8d7dcaed88a9d6160a88e6ca8d94914525b1` is authoritative-green in Tests #2987; Python 3.9 recorded 2616 passed;
- Phase357 was collision-checked immediately before creation.

This phase intentionally remains an open, unmerged stacked pull request.
