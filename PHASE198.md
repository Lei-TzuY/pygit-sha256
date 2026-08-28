# Phase 198 — Per-remote negotiation includes

Phase198 completes the configuration half of Git's fetch negotiation include
policy on top of Phase197.

## Configuration

A named remote may carry one or more values:

```text
[remote]
origin.negotiationInclude = refs/heads/release
origin.negotiationInclude = refs/heads/integration/*
```

When a fetch has no command-line `--negotiation-include`, those ordered values
are resolved with the same exact-ref / SHA-256-prefix / full-ref-glob rules used
by Phase197 and their exact commit tips are added to the upload-pack `have` set.

An explicit command-line `--negotiation-include` completely replaces the
configured fallback for that invocation, matching current Git documentation.
`--negotiation-restrict` is applied first; configured or explicit include tips
are then added to the resulting have set.

## Remote identity

The configuration is tied to the **named remote**, not merely its URL.
Phase198 therefore carries a command-local remote identity context through the
existing fetch orchestrator:

- ordinary named fetches use that remote's values;
- `--multiple`, `--all`, remote groups, and `fetch.all=true` switch the context
  independently for every member;
- two remotes that happen to share the same URL still retain different include
  policies;
- direct `http://` / `https://` URL fetches have no named-remote context and do
  not consume `remote.<name>.negotiationInclude` accidentally.

A malformed configured tip fails only the fetch of the remote that owns it.
This preserves multi-remote failure aggregation instead of rejecting unrelated
members before their turn.

## Refetch composition

Phase196 `--refetch` deliberately sends an empty `have` set to request a fresh
transfer. Per-remote negotiation include configuration is therefore inactive
under `--refetch`. Explicit negotiation arguments supplied with `--refetch`
remain validated by Phase197, but refetch's no-have policy wins for transport.

## Protocol boundary and negotiate-only

Current Git documentation defines `--negotiate-only` as printing common
ancestors without fetching. A native Git 2.47.3 packet probe performed while
preparing this phase showed an important transport boundary: forcing protocol
v0 produces

```text
warning: --negotiate-only requires protocol v2
```

pygit's current smart-HTTP client is intentionally protocol-v0. Phase198 does
not fake a negotiate-only result locally and does not claim protocol-v2
support. A later transport phase should add genuine protocol-v2 capability
advertisement / fetch commands before exposing `--negotiate-only`.

## SHA-256-native design

Configured expressions resolve against pygit's local SHA-256 refs and object
graph. The existing Phase197 planner converts only selected commit identities
to native SHA-1 for upload-pack `have` lines. No local ref, object, FETCH_HEAD,
pack, or native-map format changes.

## Tests

`tests/test_phase198.py` covers:

- ordered and duplicate configuration values;
- active named-remote inclusion;
- distinct policies for remotes sharing one URL;
- direct URL isolation;
- CLI include precedence over config;
- remote-local invalid-config failures;
- automatic activation without CLI negotiation options; and
- `--refetch` precedence over configured includes.
