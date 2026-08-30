# Phase 312 — Add protocol-v2 filtered fetch transport

Phase312 adds the Git protocol-v2 `filter <filter-spec>` request primitive on
top of the exact-green Phase309 transport line.  It is deliberately transport
only: it can receive a partial pack, but it does not persist promisor state,
invent omitted objects, or trigger lazy materialization.

## Transport API

`pygit.protocol_v2_filter_fetch` adds:

- `normalize_filter_spec(filter_spec)`;
- `build_filtered_fetch_request(...)`;
- `SmartHttpV2FilterFetchClient.fetch_filtered(...)`.

The implementation is additive.  Phase309's capability discovery, `ls-refs`,
Smart HTTP media-type checks, strict fetch parser/state machine, OID validation,
sideband handling, and `PackParser` remain byte-for-byte untouched.

A filtered fetch is one-shot and terminating: the request contains the ordinary
validated wants/haves/shallow arguments, exactly one `filter` argument, then
`done`.  The response therefore remains subject to Phase309's normal terminating
fetch contract and must contain a valid packfile.

## Filter capability and filter-spec handling

Git protocol-v2 permits a `filter <filter-spec>` argument only when the server
advertises the `filter` feature on the `fetch` command.  Phase312 fails before
transport if that feature is absent.

Filter syntax is defined by Git's object-filter machinery and can grow over
time, so Phase312 does not freeze a narrow whitelist of filter names.  It keeps
the boundary forward-compatible while rejecting framing-unsafe empty,
whitespace/control-containing, or overlong filter specs.

The known `blob:limit=<n>[kmg]` form receives stricter validation and follows
Git's interoperability recommendation for process-to-process communication:
scaled integers are expanded before transmission.  For example:

- `blob:limit=1k` -> `blob:limit=1024`;
- `blob:limit=2m` -> `blob:limit=2097152`;
- `blob:limit=1g` -> `blob:limit=1073741824`.

Other safe specs such as `blob:none`, `tree:2`, `object:type=tree`, encoded
`combine:` filters, and future server-understood filter names are forwarded
without semantic reinterpretation.

## Native Git compatibility

A local Git 2.47.3 stateless-rpc probe configured the server with
`uploadpack.allowFilter=true` and observed `fetch=shallow wait-for-done filter`
in its protocol-v2 capability advertisement.

A terminating request containing `filter blob:none` returned a normal
`packfile` section followed by `flush-pkt`.  After extracting and indexing the
pack, native `verify-pack` showed exactly one commit and one tree and no blob.

`tests/test_phase312.py` repeats the native round trip using the CI runner's Git,
then feeds the response through pygit's existing `parse_fetch_response()` and
`PackParser`.  The expected parsed native object types are exactly `commit` and
`tree`, proving that the filter has observable pack semantics rather than merely
correct request spelling.

## SHA-256-native and promisor invariants

No repository-visible identity semantics change.

- request wants/haves remain genuine full 40-hex remote-native SHA-1 OIDs;
- filtered pack entries remain native Git objects until the existing importer
  boundary converts content into repository-local SHA-256 identity;
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is introduced;
- no metadata-only native-to-local object mapping is created;
- this transport layer does not write `.pygit/promisor.json`;
- omitted objects are not represented by fabricated local OIDs and are not
  demand-fetched by the filter transport itself.

Persistent promises and later exact-native-OID materialization remain separate
repository-layer concerns.

## Coordination

- phase base: Phase309 / PR #285 exact-green head
  `34ce0a59431a5743d1bd9d725d51eb617c867789`;
- Phase309 Tests #2687: Python 3.9 / 3.13 both 2303 passed, Git 2.55.0;
- Phase310 independently hardens persistent promisor identity maps and does not
  modify this transport module;
- Phase311 independently implements `ref-in-want` on the same Phase309 base and
  is not modified by Phase312;
- repository searches before branch creation found no existing `filter_spec`,
  `allowFilter`, or dedicated protocol-v2 filtered-fetch implementation;
- Phase312 and Phase313 were both rechecked as free before Phase312 branch
  creation.

The local container still cannot clone the GitHub repository because outbound
DNS resolution for github.com is unavailable.  Native Git probes run against a
locally created repository; the complete project suite on the exact Phase312
head is therefore gated by GitHub Actions.

The Phase312 PR remains open and unmerged.
