# Phase 310 — Validate persistent promisor identity maps

Phase310 extends the fail-closed promisor metadata boundary beyond the Phase299 `sizes` side channel. Persistent `promisor.json` identity-bearing maps are now validated before they can influence repository behavior.

## What changes

- `promised` must be a JSON object whose keys are full 40-hex remote-native SHA-1 OIDs and whose values are non-empty string kinds.
- `resolved` must be a JSON object mapping full 40-hex remote-native SHA-1 OIDs to full 64-hex local SHA-256 OIDs.
- `sizes` keeps the existing Phase299 full-native-OID and non-negative-integer validation.
- malformed identity maps fail closed on read instead of flowing deeper into partial-clone behavior.
- update inputs are validated before existing state is mutated or rewritten.
- direct state writes validate the identity-bearing maps as well.

## SHA-256-native boundary

This phase does not translate identities. Remote promises remain native SHA-1 names. A resolved entry may point to a local SHA-256 identity only when it is already a full 64-hex local object id; this metadata validation does not create that object and does not derive SHA-256 from SHA-1.

There is still no SHA-1 padding, truncation, surrogate SHA-256, metadata-only content materialization, or implicit native-to-local conversion.

## Coordination

Phase310 is based on the Phase309 integration head `34ce0a59431a5743d1bd9d725d51eb617c867789`. It deliberately touches only `pygit/promisor.py` plus focused tests/documentation, avoiding Phase309's active protocol-v2 parser integration surface.

The actual repository `main` at branch creation remains `bfcbae64e4dc9997b915c16e1aa923a951090083`. Phase310 was confirmed unoccupied before branch creation.
