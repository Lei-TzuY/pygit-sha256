# Phase 308 — Integrate strict protocol-v2 record and fetch state machines

Phase308 combines the two exact-green protocol-v2 hardening siblings on one continuation:

- Phase306 / PR #283: strict capability, `ls-refs`, and object-info textual record trust layers;
- Phase307 / PR #284: strict `fetch` section and negotiation state machine.

The integration is deliberately mechanical first and behavioral second. The five Phase306-owned files are reused byte-for-byte from exact head `d4d594521f3dcb85dff870409602649fd5dd55d7` on top of Phase307 exact head `d7620c6e1e16252187e8e8011b8151414e66b99c`, then Phase308 adds cross-layer regressions through the real Smart HTTP fetch client.

## Integrated trust boundary

A protocol-v2 fetch path now composes the following checks in order:

1. expected Smart HTTP response media type;
2. complete pkt-line command envelope and required final flush;
3. strict capability textual grammar, including Git's broader documented `agent` exception;
4. strict `ls-refs` record structure and duplicate/attribute validation;
5. strict fetch section ordering and delimiter placement;
6. ACK / NAK / `ready` state validation and request-mode contracts;
7. native SHA-1 OID, scalar metadata, sideband, and pack semantic validation.

A valid earlier layer therefore cannot make a malformed later layer trusted. Conversely, malformed discovery or ref metadata fails before the next command POST is issued.

## Phase308 cross-layer coverage

`tests/test_phase308.py` exercises the composition rather than retesting isolated parsers:

- a complete strict capability discovery -> strict `ls-refs` -> strict `done` fetch succeeds through `SmartHttpV2FetchClient.fetch()`;
- malformed capability syntax stops before `ls-refs` or `fetch` POSTs;
- duplicate `ls-refs` output stops before the fetch POST;
- valid discovery and refs do not weaken the fetch rule that `ready` requires a packfile in the same response;
- strict discovery and refs compose with a valid `wait-for-done` ACK-only negotiation, with no `done` request line and no premature pack transition.

The responses expose actual Content-Type headers so the tests exercise the Smart HTTP MIME boundary as well as parser/state-machine behavior.

## Git compatibility

The integration preserves the exact native-compatible behavior already proven independently:

- protocol-v2 textual pkt-lines accept either no terminal LF or exactly one terminal LF;
- malformed repeated/embedded LF, invalid CR/NUL, malformed capability/ref/result structure, and duplicate structural records remain rejected;
- `done` fetch responses omit acknowledgments and proceed to pack transfer;
- `wait-for-done` responses remain acknowledgments-only until a later `done`;
- `ready` requires the packfile in the same response;
- native Git 2.55.0 probes from Phase303/304/307 remain part of the full inherited suite.

No native behavior is relaxed merely to make the two sibling implementations coexist.

## SHA-256-native invariants

This phase changes validation composition only; it does not introduce a new object materialization path.

- remote protocol-v2 transport OIDs remain genuine full 40-hex native SHA-1 values;
- repository-visible object identities remain content-derived local 64-hex SHA-256 values;
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is introduced;
- no metadata-only native-to-local object mapping is created;
- no additional fetch or demand-materialization behavior is triggered by validation.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- Phase306 exact-green head: `d4d594521f3dcb85dff870409602649fd5dd55d7`, Tests #2670, Python 3.9 / 3.13 both 2273 passed, Git 2.55.0;
- Phase307 exact-green head and Phase308 base: `d7620c6e1e16252187e8e8011b8151414e66b99c`, Tests #2680, Python 3.9 / 3.13 both 2278 passed, Git 2.55.0;
- Phase305 / PR #282 is an overlapping predecessor and is not used as an integration source;
- Phase308 was rechecked before modification and still pointed exactly at the Phase307 head;
- Phase309 was free at the phase-start collision check.

The initial Git tree write was rejected before any ref movement because an earlier cached Phase307 tree SHA was inaccurate. The exact Phase307 commit was re-read, yielding tree `28be8101114f8752b0c9d49e4e2eda06059256b8`, after which the Phase306 blobs were integrated without byte drift.

Phase308 is complete only after the exact final head passes the full Python 3.9 / 3.13 GitHub Actions matrix. The PR remains open and unmerged.
