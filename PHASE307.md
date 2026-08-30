# Phase 307 — Reconcile strict fetch state-machine compatibility

Phase307 keeps Phase305's strict protocol-v2 fetch state machine intact while reconciling two historical regression fixtures exposed by its first full CI run.

## What the failed CI showed

Phase305 Tests #2666 reached 2273 passed / 2 failed on Python 3.13 before fail-fast cancelled Python 3.9.

The failures were both legacy expectations rather than evidence that the new wire grammar should be weakened:

1. the Phase200 ACK-only fixture encoded `ACK <oid>`, `ready`, then an immediate `flush-pkt` with no packfile;
2. the Phase201 negotiate mock expected the old generic `RuntimeError` when a `wait-for-done` exchange unexpectedly transitioned to ready/pack.

## Git compatibility authority

Current Git protocol-v2 defines:

- an acknowledgments-only response may end with `flush-pkt` when negotiation continues;
- if the server sends `ready`, it has selected a cut point and the packfile is in the packfile section of the same response;
- `wait-for-done` instructs the server to never send `ready` and to wait for a later `done` before sending a packfile.

Phase307 therefore does not relax the Phase305 production parser.

Instead:

- the historical ACK-only test now uses an actual ACK-only response without `ready`;
- the historical negotiate transition test expects the specific protocol `ValueError` emitted by the request-mode validator;
- new Phase307 regressions explicitly pin both sides of the contract: ACK+ready without pack is rejected, ACK+ready followed by pack is accepted, and wait-for-done rejects ready/pack.

## SHA-256-native invariants

No identity or object-import behavior changes.

- fetch transport OIDs remain genuine full 40-hex remote-native SHA-1 identities;
- repository-visible objects remain content-derived SHA-256;
- no SHA-1 padding, truncation, translation, surrogate SHA-256, metadata-only native-to-local mapping, or new materialization path is introduced.

## Coordination

- base candidate: Phase305 / PR #282 head `87505030bf798a6ec463f5427f4f1a7c0fada8e3`;
- Phase305 first CI run: Tests #2666, Python 3.13 `2273 passed / 2 failed`, Python 3.9 cancelled by fail-fast;
- Phase306 / PR #283 is an independent record-grammar integration and is not modified here;
- Phase307 was confirmed free before creation.

Phase307 is complete only after a full Python 3.9 / 3.13 GitHub Actions matrix passes on the exact final head. If exact-green, it supersedes the failed Phase305 candidate without merging it.
