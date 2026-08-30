# Phase 302 — Integrate promisor identity and strict fetch framing hardening

Phase302 reconciles three exact-green sibling lines on top of the Phase298 shared Smart HTTP envelope work without rewriting any validated branch.

## Integrated invariants

The resulting stack preserves all of the following at once:

- Phase298 shared Smart HTTP protocol-v2 response-envelope validation for capability discovery, `ls-refs`, and object-info;
- Phase299 full remote-native SHA-1 validation for persisted promisor `sizes` keys;
- Phase300 complete protocol-v2 `fetch` response framing with a required final `flush-pkt`.

Phase299 and Phase300 production/test/document blobs are reused exactly from their validated branches where possible rather than being manually reimplemented.

## Cross-layer regression coverage

`tests/test_phase302.py` proves that these sibling changes compose rather than merely coexist in separate files.

1. A metadata-only promisor refresh learns a size for a genuine 40-hex remote-native OID, persists it through the hardened Phase299 side channel, and leaves the trusted value unchanged when a later 64-hex local SHA-256 surrogate is rejected.
2. A `SmartHttpV2FetchClient` accepts a correctly typed Phase298 protocol-v2 discovery response, then passes a truncated fetch body into the Phase300 parser where missing final `flush-pkt` is rejected.

This keeps HTTP envelope trust, native metadata identity trust, and command-specific pkt-line framing as independent fail-closed layers.

## Git / SHA-256 boundary

No identity translation is introduced.

- protocol transport and promisor metadata keys remain genuine remote-native full 40-hex SHA-1 identities;
- repository-visible objects remain content-derived local SHA-256 identities;
- local 64-hex SHA-256 values are not accepted as remote size metadata keys;
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is used;
- no new content materialization or native-to-local mapping path is added.

## Coordination

- actual `main`: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- base: Phase298 / PR #277 exact-green head `ec338442501b5fc6cb66c77e9b6b8c75365081a0`;
- Phase298 Tests #2626: Python 3.9 / 3.13 both 2221 passed, Git 2.55.0;
- Phase299 / PR #275 is an exact-green sibling from Phase297 and modifies only `promisor.py` plus its tests/docs;
- Phase300 / PR #276 is an exact-green sibling from Phase297 and modifies only `protocol_v2_fetch.py` plus its tests/docs;
- Phase301 is independently occupied by parallel work and is intentionally untouched;
- Phase302 was confirmed free immediately before creation.

Phase302 must pass its own complete Python 3.9 / 3.13 GitHub Actions matrix before either sibling PR is marked superseded.
