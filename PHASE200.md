# Phase 200 — protocol-v2 fetch and negotiate-only

Phase 199 introduced protocol-v2 capability discovery and `ls-refs`. Phase 200
adds the corresponding smart-HTTP `fetch` command and uses it as a real object
transfer path when a repository selects `protocol.version=2`.

## Implemented

- protocol-v2 `fetch` request framing with:
  - `command=fetch`
  - command/capability delimiter packets
  - `want` and `have` lines
  - `done`
  - `no-progress`
  - `ofs-delta`
  - optional `include-tag`
  - `wait-for-done` validation
- sectioned fetch response parsing for:
  - `acknowledgments`
  - `shallow-info`
  - `wanted-refs`
  - `packfile`
- packfile sideband handling:
  - channel 1: pack bytes
  - channel 2: progress
  - channel 3: fatal server error
- `SmartHttpV2FetchClient` for stateless smart-HTTP object transfer.
- command-scoped routing of the established fetch stack through v2 when
  `protocol.version=2` is configured.
- transparent fallback to the existing protocol-v0 transport when a server
  ignores an ordinary v2 preference and answers the handshake as v0.
- `fetch --negotiate-only` using genuine protocol-v2 ACK negotiation.
- SHA-256 user-facing output for negotiate-only even though `have` / `ACK`
  identities on the remote wire remain native Git SHA-1.

## `--negotiate-only`

The command requires at least one `--negotiation-restrict` (or historical
`--negotiation-tip`) expression. It sends the corresponding native SHA-1 have
set, asks the server for common commits, and prints the matching local SHA-256
commit IDs.

Unlike an ordinary v2-preferred fetch, negotiate-only does **not** fall back to
protocol v0. It also requires the server's protocol-v2 `fetch=wait-for-done`
feature. That feature guarantees that the server cannot decide to send a
packfile before the client explicitly sends `done`; negotiate-only deliberately
never sends `done`.

This phase keeps negotiate-only to one repository/URL source and rejects
multi-remote, prefetch, refetch, set-upstream, and explicit fetch-refspec
composition. Those combinations would require additional output/transaction
semantics and are not silently approximated.

## Compatibility notes

The current Git protocol-v2 specification defines `fetch` as a command whose
response is divided into delimiter-separated sections. When a packfile is sent,
its section is always multiplexed with sideband channel bytes. The
`wait-for-done` feature instructs the server never to emit `ready`/a packfile
until the client sends `done`.

Current `git fetch` documentation defines `--negotiate-only` as printing common
ancestors from the provided negotiation restriction/tip arguments without
fetching objects. A native Git 2.47.3 probe from Phase 198 also established that
forcing protocol v0 rejects `--negotiate-only`, which is why Phase 200 builds on
the genuine v2 command rather than emulating the result locally.

## Architecture

`pygit.protocol_v2` owns v2 packet framing, response sections, and the HTTP v2
query/fetch clients. `pygit.fetch_protocol_v2` is a command-scoped adapter: it
temporarily routes the existing `SmartHttpClient` API through v2, so all mature
configured/direct fetch, refspec, tag, prune, atomic, dry-run, refetch, and
negotiation planning continues to live in one higher-level stack.

The protocol preference scope is entered before the Phase 196–198 transport
wrappers. Their have-set policy therefore sees the v2-aware method as the
underlying fetch operation instead of requiring a second implementation.

## SHA-256-native boundary

No repository-visible object format changes. Local objects, refs, FETCH_HEAD,
reflogs, and printed negotiate-only commits remain SHA-256. Native SHA-1 object
IDs appear only in protocol-v2 `ls-refs`, `want`, `have`, `ACK`, and pack
interoperability at the smart-HTTP boundary.

## Regression coverage

`tests/test_phase200.py` covers:

- fetch command/delimiter framing
- `wait-for-done` feature enforcement
- ACK-only section parsing
- shallow-info parsing
- packfile sideband reconstruction
- fatal sideband errors
- v2 HTTP fetch request and pack parsing
- ordinary v2-to-v0 fallback
- routing through the established `SmartHttpClient` API
- SHA-256 negotiate-only output
- restriction requirement
- top-level CLI output
- `protocol.version=2` activation for ordinary fetches
