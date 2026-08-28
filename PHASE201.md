# Phase 201 — protocol-v2 fetch integration and negotiate-only

Phase 200 / PR #176 deliberately isolated and tested the protocol-v2 upload-pack
`fetch` wire format without switching normal porcelain fetches. Phase 201 uses
that foundation in the mature fetch stack and adds the ACK-only exchange needed
for `fetch --negotiate-only`.

## Ordinary fetch integration

When the current repository configures:

```text
protocol.version = 2
```

Phase 201 command-scopes the existing `SmartHttpClient` API onto Phase 200's v2
query/fetch clients. Higher layers remain unchanged, including:

- configured and explicit fetch refspecs
- tag policy and automatic tag following
- pruning
- direct destination updates
- multi-source orchestration
- atomic local-ref updates
- FETCH_HEAD controls
- dry-run
- prefetch
- refetch
- negotiation restrict/include policies

The protocol scope is entered before Phase 196–198 transport wrappers. Those
wrappers therefore capture the v2-aware fetch method as their underlying
transport and continue to own their established `have`-set semantics.

A server that ignores the HTTP `Git-Protocol: version=2` request may still be
used by an ordinary fetch: Phase 201 records that URL as a v0 fallback for the
command and resumes the mature protocol-v0 path.

## `fetch --negotiate-only`

Phase 201 adds genuine v2 negotiation-only behavior rather than calculating a
result purely from local history.

The command requires at least one:

```text
--negotiation-restrict=<commit-or-ref>
```

or the historical synonym:

```text
--negotiation-tip=<commit-or-ref>
```

The selected local commit ancestry is converted to native SHA-1 `have` values
at the smart-HTTP boundary. The client requests `wait-for-done`, deliberately
does not send `done`, parses the server's common `ACK` values, and translates
those ACKs back to repository-visible SHA-256 commits for output.

Negotiate-only is intentionally stricter than an ordinary fetch:

- a v0 response is an error, not a fallback
- the server must advertise protocol-v2 `fetch`
- the server must advertise `fetch=wait-for-done`
- receiving `ready` or a packfile is rejected
- at least one negotiation restriction/tip is required
- explicit fetch refspecs are rejected
- `--refetch`, `--set-upstream`, `--all`, `--multiple`, and `--prefetch` are not
  silently approximated with negotiate-only

Explicit `--negotiation-include` tips may enlarge the reported have set used by
the exchange, while printed results remain within the restriction/tip ancestry
domain.

## Git compatibility

Git protocol v2 defines fetch responses as delimiter-separated named sections.
The Phase 200 transport already parses acknowledgments, shallow information,
wanted refs, and sideband packfile data. The `wait-for-done` fetch feature says
the server must not produce `ready` or a packfile until the client sends
`done`; this provides the protocol boundary required by negotiate-only.

Current Git fetch documentation describes `--negotiate-only` as printing the
commits common to the local negotiation-tip ancestry and the remote without
fetching objects. A prior native Git probe established that forcing protocol v0
rejects negotiate-only, so Phase 201 does not fake a v0 equivalent.

## Compatibility seam

Phase 196 regression fixtures exercise wrapper composition with a lightweight
stand-in object instead of a full `Repository`. Protocol preference is optional,
so `protocol_v2_requested()` deliberately treats an object without `config_get`
as “v2 not requested.” This preserves older wrapper call shapes while real
repositories still read `protocol.version` normally.

## SHA-256-native design

Repository objects, refs, FETCH_HEAD, reflogs and negotiate-only output remain
SHA-256. Native SHA-1 identities are limited to protocol-v2 ref discovery,
`want`, `have`, `ACK`, and pack interoperability.

## Regression coverage

`tests/test_phase201.py` covers:

- `wait-for-done` request framing and feature validation
- ACK-only client negotiation
- rejection of unexpected ready/pack transition
- optional protocol preference on wrapper stand-ins
- ordinary v2 discovery/fetch routing
- ordinary v2-to-v0 fallback
- native ACK → SHA-256 output mapping
- strict v2 requirement for negotiate-only
- top-level negotiate-only output
- `protocol.version=2` activation
- Phase 196 refetch + dry-run wrapper compatibility
