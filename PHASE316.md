# Phase 316 — direct ref-in-want filtered shallow fetch

Phase316 composes the exact-green protocol-v2 `ref-in-want`, object-filter, and shallow-cutoff transports into one direct named-ref fetch.

The new path keeps the main benefit of `ref-in-want`: after capability discovery it can fetch a named remote ref directly, without first issuing `ls-refs`.

## API

`pygit.protocol_v2_ref_filter_shallow` adds:

- `build_ref_filtered_shallow_request()`;
- `SmartHttpV2RefFilteredShallowClient.fetch_refs_filtered_shallow()`.

The builder requires all three fetch features to be advertised:

- `ref-in-want`;
- `shallow`;
- `filter`.

It also requires at least one `deepen-since` / `deepen-not` cutoff, because ordinary direct filtered fetch remains a separate, simpler composition opportunity.

## Reused trust boundaries

Phase316 does not add a second fetch parser.

It reuses:

- Phase311 `_validated_want_refs()` for safe ordered named refs and duplicate rejection;
- Phase312 `normalize_filter_spec()` for filter validation and scaled blob-limit normalization;
- Phase313 cutoff validators for non-negative timestamps and framing-safe remote revisions;
- the core native SHA-1 OID validator for `have` and existing `shallow` identities;
- Phase311 `validate_ref_in_want_response()` for the exact requested/returned wanted-ref set and terminating pack contract;
- Phase313 `validate_shallow_response_for_request()` for request-aware `unshallow` validation;
- the existing `PackParser` and importer boundary.

## Wire layout

The direct combined request is emitted as:

1. fetch command and server-options;
2. ordinary transport options (`no-progress`, `ofs-delta`, optional `include-tag`);
3. existing client `shallow <oid>` declarations;
4. optional `deepen-since <timestamp>`;
5. repeated ordered `deepen-not <rev>` records;
6. ordered `want-ref <ref>` records;
7. sorted/deduplicated native `have <oid>` records;
8. exactly one normalized `filter <filter-spec>`;
9. `done`;
10. final flush.

## Native Git compatibility

A local Git 2.47.3 stateless-rpc probe configured both:

- `uploadpack.allowRefInWant=true`;
- `uploadpack.allowFilter=true`.

One request containing `deepen-since`, `deepen-not`, `want-ref`, `filter blob:none`, and `done` succeeded. The response contained all three expected semantic surfaces:

- `wanted-refs` resolving the requested ref to its native SHA-1 tip;
- `shallow-info` reporting the cutoff boundary;
- `packfile` containing the selected non-blob objects.

The Phase316 CI test repeats this against the runner's Git 2.55.0 and feeds the full response through pygit's existing strict parser before validating the requested ref set, shallow boundary, and filtered pack object types.

## SHA-256-native / promisor invariants

No hash-domain shortcut is introduced.

- `want-ref` names and `deepen-not` values are remote transport expressions;
- returned wanted-ref and shallow-info OIDs remain genuine remote-native SHA-1 identities;
- omitted filtered objects are not assigned fabricated local object IDs;
- received object content crosses the existing importer boundary before repository-local SHA-256 identity exists;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-only native-to-local mapping;
- no persistent promisor state is written by this transport layer.

## Coordination

- exact base: Phase314 / PR #290 head `7e3d0fddede5eeb78ddbaddc783197750dcbad56`;
- Phase314 Tests #2718: Python 3.9 / 3.13 both 2353 passed, Git 2.55.0;
- Phase315 is independently occupied by `phase315-preserve-ls-refs-unborn` and is intentionally untouched;
- Phase316 was rechecked as free immediately before branch creation.

## Tests

`tests/test_phase316.py` covers:

- full cross-feature packet ordering;
- capability intersection;
- reuse of duplicate-ref, timestamp, filter-spec, and native-OID validation contracts;
- no-`ls-refs` Smart HTTP behavior;
- exact wanted-ref response validation through the combined client;
- native Git direct named-ref + shallow cutoff + `blob:none` semantics, including wanted ref resolution, shallow boundary, and commit/tree-only pack contents.

The full Python 3.9 / 3.13 GitHub Actions matrix must pass before Phase316 is exact-green.
