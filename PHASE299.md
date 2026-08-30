# Phase 299 — Validate promisor size native object identities

Phase299 hardens the persistent `promisor.json` size side-channel used by metadata-only partial-clone classification.

The `sizes` map is keyed by the remote object's native identity. In this repository's current compatibility model that identity is a full 40-hex SHA-1 object ID, while repository-visible local objects remain content-derived SHA-256.

## Change

Every `sizes` key is now validated as a full native SHA-1 identity both when new metadata is written and when persisted promisor state is read.

Accepted keys:

- exactly 40 hexadecimal characters;
- hexadecimal case is accepted because object IDs are value-equivalent regardless of presentation case.

Rejected keys include:

- abbreviated SHA-1 IDs;
- overlong values;
- 64-hex local SHA-256 IDs used as accidental metadata surrogates;
- non-hex strings;
- non-string keys passed through the in-memory update API.

Malformed persisted size metadata fails closed during `read_promisor_state()` instead of being admitted into the trusted metadata state.

## SHA-256-native boundary

This phase deliberately validates the identity namespace rather than translating it.

- remote metadata keys remain genuine 40-hex native SHA-1 IDs;
- local repository object IDs remain genuine content-derived 64-hex SHA-256 IDs;
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is introduced;
- no content materialization or native-to-local mapping is added;
- scalar object size remains the only trusted value stored by this side-channel.

## Compatibility

The on-disk schema version remains `1`. Existing valid Phase276+ state is unchanged. Legacy version-1 files that omit `sizes` still receive the existing empty-map default.

The validation is intentionally scoped to `sizes`; Phase299 does not redefine the historical `promised` or `resolved` map schema.

## Regression coverage

`tests/test_phase299.py` covers:

1. acceptance of a full 40-hex native SHA-1 key;
2. rejection of abbreviated, overlong, non-hex, and arbitrary keys;
3. explicit rejection of a 64-hex local SHA-256 surrogate in the size channel;
4. fail-closed reading of malformed persisted size keys;
5. no partial state write when update-time size-key validation fails.

The full historical suite remains required on Python 3.9 and Python 3.13.

## Coordination

Phase298 is independently occupied by shared protocol-v2 Smart HTTP envelope validation and modifies the transport layer. Phase299 therefore starts from the exact-green Phase297 head and touches only the promisor metadata trust boundary, avoiding a conflicting rewrite of the active Phase298 branch.
