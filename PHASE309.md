# Phase 309 — Integrate strict protocol-v2 record and fetch state machines

Phase309 creates one clean continuation containing the exact-green capability / `ls-refs`, object-info, and fetch state-machine hardening that was developed across the Phase303–305 sibling lines.

The branch uses the final exact-green Phase305 / PR #282 head as its base because that version preserves the established public `SmartHttpV2FetchClient.negotiate()` pack-transition `RuntimeError` contract while retaining the strict wire parser. Phase303 and Phase306 files are then reused by exact blob identity rather than manually reimplemented.

## Integrated protocol-v2 trust boundary

The transport now composes the following fail-closed layers:

1. Smart HTTP discovery/result media types;
2. complete command-specific pkt-line framing and final flush validation;
3. capability, `ls-refs`, object-info, and fetch textual-record grammar;
4. fetch section ordering, delimiter placement, and request-mode state validation;
5. native SHA-1 OID / scalar metadata / pack semantic validation;
6. the existing importer boundary that creates repository-visible SHA-256 objects only from actual fetched content.

### Capability and `ls-refs`

Phase303 contributes strict capability ABNF, Git's broader printable-ASCII `agent` exception, zero-or-one terminal-LF compatibility, embedded/repeated LF rejection, duplicate ref rejection, strict `symref-target`/`peeled` structure, and forward-compatible preservation of unknown syntactically valid fields.

### Object-info

Phase304/306 retain real Git 2.55 compatibility for no-LF object-info records while rejecting CR/CRLF, repeated/embedded LF, non-ASCII result records, extra fields, malformed ordering, duplicate results, and requested/result-set mismatches.

### Fetch

The final Phase305 head contributes strict ordered fetch sections, delimiter rules, ACK/NAK/ready semantics, duplicate/conflicting shallow and wanted-ref rejection, zero-or-one LF text parsing, `done` and `wait-for-done` request-mode validation, and native Git stateless-rpc probes.

It also preserves the historical public `negotiate()` error contract: an unexpected ready/pack transition still raises `RuntimeError`, even though the lower-level request-mode validator remains a strict protocol `ValueError` layer.

## Phase309 cross-layer regressions

`tests/test_phase309.py` proves the combined control flow rather than only the file union:

- a full Smart HTTP `discovery -> ls-refs -> fetch -> pack parser` exchange succeeds with legal no-LF textual records and an extended printable agent;
- duplicate `ls-refs` results fail before the fetch POST is issued;
- `ready` without a same-response packfile is rejected;
- `ready` followed by a packfile is accepted at the wire-parser layer;
- public negotiate-only pack-transition behavior remains the established `RuntimeError` contract.

## SHA-256-native invariants

No identity shortcuts are introduced.

- remote transport and metadata OIDs remain genuine full 40-hex SHA-1 identities;
- repository-visible objects remain content-derived local SHA-256 identities;
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is used;
- no metadata-only native-to-local mapping is added;
- no metadata parser may create a local object or materialize promised content.

## Coordination

- actual `main`: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- base: final Phase305 / PR #282 exact-green head `f8f2c05b2941a1453b645abac1ea29a98e5f6999`;
- Phase305 Tests #2677: Python 3.9 / 3.13 both 2275 passed, Git 2.55.0;
- Phase306 / PR #283 exact-green head `d4d594521f3dcb85dff870409602649fd5dd55d7`, Tests #2670: Python 3.9 / 3.13 both 2273 passed;
- Phase307 / PR #284 exact-green alternative head `d7620c6e1e16252187e8e8011b8151414e66b99c`, Tests #2680: Python 3.9 / 3.13 both 2278 passed;
- Phase308 is independently occupied and is untouched;
- Phase309 was confirmed free immediately before branch creation.

Phase309 is complete only after its own full Python 3.9 / 3.13 GitHub Actions matrix passes on the exact final head.
