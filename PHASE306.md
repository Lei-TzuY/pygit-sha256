# Phase 306 — Integrate strict protocol-v2 record trust layers

Phase306 cleanly combines the exact-green Phase303 capability / `ls-refs` record grammar hardening with the exact-green Phase304 `object-info size` textual-record hardening.

The two phases were developed as siblings from Phase301 and intentionally touched separate production files. Phase306 uses the Phase304 exact-green head as its base and reuses the exact Phase303 blobs for `pygit/protocol_v2.py`, `tests/test_phase303.py`, and `PHASE303.md`, avoiding a manual rewrite of either validated branch.

## Integrated trust boundary

Protocol-v2 Smart HTTP metadata now has four explicit validation layers before higher-level code may trust it:

1. Smart HTTP media type validation;
2. command-specific pkt-line envelope / final flush validation;
3. capability, `ls-refs`, and `object-info` textual record grammar validation;
4. native OID / object-size semantic validation.

A failure in an earlier layer cannot be normalized into a valid-looking later record.

### Capability and `ls-refs`

Phase303 contributes:

- zero-or-one terminal LF compatibility instead of unrestricted `rstrip()` normalization;
- protocol-v2 capability key/value grammar validation;
- Git's broader printable-ASCII `agent` exception;
- unknown syntactically valid capability retention;
- embedded/repeated LF and NUL rejection;
- duplicate `ls-refs` record rejection;
- duplicate/empty `symref-target` and duplicate `peeled` rejection;
- preservation of unknown `ls-refs` attributes for forward compatibility.

### `object-info size`

Phase304 contributes:

- native Git no-LF and documented single-LF textual record compatibility;
- CR/CRLF, embedded/repeated LF, non-ASCII, and extra-field rejection;
- strict `size` header ordering and duplicate handling;
- exact requested-OID/result-set matching;
- existing complete flush framing and Smart HTTP result MIME validation.

## Cross-layer regression

`tests/test_phase306.py` exercises the real `SmartHttpV2ObjectInfoClient.query_sizes()` flow across both sibling hardenings:

- a valid no-LF capability advertisement with an extended printable agent flows through to a valid no-LF object-info response;
- a malformed capability value stops the exchange before the object-info POST is issued;
- valid capabilities do not weaken the strict object-info result grammar, and an extra result field still fails after body read.

This ensures the combined branch is more than a file-level union: the trust layers compose in the actual HTTP client control flow.

## Git and SHA-256 invariants

No object identity semantics change.

- transport and metadata object IDs remain genuine full 40-hex remote-native SHA-1 identities;
- repository-visible local objects remain content-derived SHA-256;
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is introduced;
- no metadata-only native-to-local mapping or content materialization path is added.

## Coordination

- actual `main`: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- common ancestor: Phase301 / PR #279 exact-green head `f2f1eb0c2426dafa6de8f655f45870bdfd64689d`;
- Phase303 / PR #281 exact-green head `3cced2c7eebe83cd48541815dfda962b72230509`, Tests #2661: Python 3.9 / 3.13 both 2265 passed, Git 2.55.0;
- Phase304 / PR #280 exact-green head `f0093f61d2e18aafd2279cebcd01fdee156c1207`, Tests #2659: Python 3.9 / 3.13 successful, Git 2.55.0;
- Phase305 branch is independently occupied and is not rewritten by this phase;
- Phase306 was confirmed free immediately before branch creation.

Phase306 is complete only after its own full Python 3.9 / 3.13 GitHub Actions matrix passes on the exact final head.
