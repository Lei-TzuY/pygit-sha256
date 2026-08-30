# Phase 314 — integrate protocol-v2 filtered shallow cutoffs

Phase314 composes two exact-green transport features into one real protocol-v2 fetch path:

- Phase312 object filtering (`filter <filter-spec>`);
- Phase313 time/revision shallow cutoffs (`deepen-since` / repeated `deepen-not`).

The integration is based on the exact-green Phase313 head and reuses the exact Phase312 filter implementation/tests/documentation rather than rewriting them.

## Combined API

`pygit.protocol_v2_filter_shallow` adds:

- `build_filtered_shallow_cutoff_fetch_request()`;
- `SmartHttpV2FilteredShallowClient.fetch_filtered_shallow()`.

The combined builder delegates shallow/cutoff validation and request layout to Phase313, delegates filter-spec validation/normalization to Phase312, and only joins the two exact-green primitives.

The resulting request order is:

1. ordinary fetch command/options;
2. existing `shallow <oid>` declarations;
3. optional `deepen-since <timestamp>`;
4. repeated `deepen-not <rev>`;
5. wants and haves;
6. exactly one normalized `filter <filter-spec>`;
7. `done`;
8. final flush packet.

## Response trust boundary

The combined Smart HTTP client retains all existing response layers:

- Phase309 strict fetch section ordering and final-flush framing;
- terminating `done` response requires a valid packfile;
- Phase313 request-aware `unshallow` validation;
- existing sideband handling and `PackParser` validation.

No response parser is copied or relaxed.

## Native Git compatibility

Local Git 2.47.3 accepted a single request containing:

- `deepen-since 1704067200`;
- `deepen-not refs/heads/old`;
- a normal `want`;
- `filter blob:none`;
- `done`.

The response contained `shallow-info` followed by `packfile`. The shallow boundary was the expected requested tip after the exclusion, and the filtered pack contained exactly commit + tree with no blob.

`tests/test_phase314.py` repeats this combined stateless-rpc round trip using the CI runner's Git version and parses the result through pygit's strict response parser and `PackParser`.

## Exact-green reuse

Phase314 starts from Phase313 / PR #289 exact-green head:

`b7a7ff3ce35748207616ef0f991a44a7b09f42ac`

The Phase312 artifacts are copied byte-for-byte from PR #288 head:

`98399094736721835204fcca92d22c6a14d47b5f`

Retained Phase312 files:

- `pygit/protocol_v2_filter_fetch.py`;
- `tests/test_phase312.py`;
- `PHASE312.md`.

Their blob identity is checked during branch review to ensure the integration does not silently rewrite the independently validated filter implementation.

## SHA-256-native / promisor boundary

No identity translation or promise persistence is added.

- transport wants/haves/shallow-info remain remote-native SHA-1 identities;
- `deepen-not` remains a remote revision expression;
- filtered omitted objects are not assigned fabricated local identities;
- received objects reach the existing importer boundary before repository-local SHA-256 identity exists;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-only native-to-local mapping;
- no `.pygit/promisor.json` write is performed by this transport integration.

## Coordination

- Phase311 / PR #287: exact-green ref-in-want base line;
- Phase312 / PR #288: exact-green filtered fetch sibling;
- Phase313 / PR #289: exact-green shallow cutoff line, base for Phase314;
- Phase310 / PR #286 remains an independent promisor-state line and is intentionally not included while its exact head is not green;
- Phase314 was collision-checked immediately before branch creation.

## Tests

The integration retains the complete Phase312 and Phase313 suites and adds Phase314 coverage for:

- exact cross-feature packet ordering;
- filter + shallow capability intersection;
- preservation of component validation contracts;
- request-aware unshallow rejection through the combined Smart HTTP client;
- successful combined Smart HTTP result handling;
- native Git `blob:none + deepen-since + deepen-not` semantics, including both shallow boundary and pack object types.

The full Python 3.9 / 3.13 GitHub Actions matrix must pass before Phase314 is exact-green.
