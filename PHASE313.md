# Phase 313 — protocol-v2 deepen-since / deepen-not shallow cutoffs

Phase313 adds Git protocol-v2 shallow fetch cutoffs by timestamp and excluded remote revision on top of the exact-green Phase311 line.

## Scope

The new additive module `pygit.protocol_v2_shallow_cutoff` provides:

- `build_shallow_cutoff_fetch_request()`
- `validate_shallow_response_for_request()`
- `SmartHttpV2ShallowCutoffClient.fetch_shallow()`

It intentionally does not modify the exact-green Phase309/311 fetch parser or state machine.

## Git protocol behavior

The current Git protocol-v2 fetch grammar permits these shallow arguments when `fetch=... shallow` is advertised:

- `deepen-since <timestamp>` — cut history by commit time instead of numeric depth;
- repeated `deepen-not <rev>` — exclude history reachable from named remote revisions;
- `deepen-since` and `deepen-not` may be combined;
- neither may be combined with numeric `deepen`.

Native Git 2.47.3 probes additionally confirmed:

- `deepen-since 0` is accepted;
- negative, non-numeric, and partially numeric timestamps are rejected;
- short remote revision names such as `old`, full refs such as `refs/heads/old`, and `HEAD` are resolved by the server;
- missing remote revisions fail on the server rather than being guessed locally;
- repeated `deepen-not` records are accepted;
- `deepen-since + deepen-not` produces a normal `shallow-info` section followed by `packfile`.

The CI suite repeats real stateless-rpc shallow fetches against the runner's Git version.

## Composition

Phase313 reuses `build_fetch_request()` for:

- fetch capability checks;
- remote-native 40-hex SHA-1 want/have validation;
- existing shallow OID validation;
- `wait-for-done` semantics;
- `server-option` ordering;
- `no-progress`, `ofs-delta`, and `include-tag` framing;
- command delimiter and final flush framing.

The module then inserts validated `deepen-since` / `deepen-not` records immediately before the first `want` packet. This keeps the established request builder authoritative instead of cloning its state machine.

## Request-aware shallow response validation

The generic fetch parser can validate the syntax and internal consistency of `shallow` / `unshallow` records, but it cannot know what the client declared in its request.

Phase313 adds the missing request-aware rule: a server response may only send `unshallow <oid>` for an OID that the client supplied as `shallow <oid>` in that request. An unrequested `unshallow` is rejected before higher-level repository state is changed.

## SHA-256-native boundary

No hash-domain translation is introduced.

- fetch wants/haves and shallow-info OIDs remain genuine remote-native SHA-1 values;
- `deepen-not` values remain remote revision expressions and are never converted into local object IDs;
- received pack objects continue through the existing importer boundary;
- repository-visible identities remain content-derived local SHA-256;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-only native-to-local mapping is added.

## Coordination

- base: Phase311 / PR #287 exact-green head `5f39321a8581ae97de7d4c1ef413e47771046499`;
- Phase311 Tests #2700: Python 3.9 / 3.13 both 2311 passed, Git 2.55.0;
- Phase310 / PR #286 remains an independent promisor-state line and is not used as a base;
- Phase312 / PR #288 independently implements protocol-v2 filtered fetch and is not used as a base;
- Phase313 was confirmed free immediately before branch creation.

## Tests

`tests/test_phase313.py` covers:

- ordered `shallow` → `deepen-since` → repeated `deepen-not` → `want` emission;
- non-negative timestamp validation including native-compatible zero;
- unsafe `deepen-not` framing rejection while preserving remote revision resolution;
- shallow capability gating;
- request-aware `unshallow` validation;
- Smart HTTP client composition through the existing strict fetch state machine;
- native Git stateless-rpc `deepen-since` and combined `deepen-since + deepen-not` round trips.

The full Python 3.9 / 3.13 GitHub Actions matrix must pass before this phase is exact-green.
