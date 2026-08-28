# Phase 202 — protocol-v2 shallow/deepen fetch controls

Phase 202 extends the Phase200/201 protocol-v2 fetch stack with real shallow
request/response semantics for normal `pygit fetch` commands.

## Scope

With a named remote and:

```text
protocol.version = 2
```

`pygit fetch` now accepts:

```text
--depth=<n>
--deepen=<n>
--unshallow
```

The outer fetch wrapper strips these options before the established Phase183–201
parser and activates a command-scoped shallow transport policy.

### `--depth=<n>`

Sends protocol-v2:

```text
deepen <n>
```

with absolute depth semantics relative to the requested remote tips.

### `--deepen=<n>`

Requires an existing `.pygit/shallow` boundary and sends:

```text
shallow <native-oid>
...
deepen <n>
deepen-relative
```

so the requested depth is relative to the client's current shallow boundary.

### `--unshallow`

Requires an existing shallow repository and requests Git's special infinite
depth value:

```text
deepen 2147483647
```

while still advertising every current `shallow <native-oid>` boundary.

## Protocol-v2 compatibility

Current Git protocol-v2 documentation requires the server to advertise the
`shallow` fetch feature before a client sends shallow/deepen arguments. Phase202
validates that capability rather than sending unsupported arguments.

Every local shallow boundary is sent to the server as native Git SHA-1. The
server may answer with a `shallow-info` section containing:

```text
shallow <oid>
unshallow <oid>
```

The Phase200 parser already recognized these lines; Phase202 now preserves them
on the fetch result and makes them operational.

Ordinary protocol-v2 fetches may continue to fall back to protocol v0 when a
server ignores `Git-Protocol: version=2`. A shallow-controlled fetch does **not**
fall back: pygit's protocol-v0 transport has no shallow negotiation support, so
silently continuing would claim a depth change that never happened.

## SHA-256-native shallow state

`.pygit/shallow` remains repository-local metadata and therefore stores pygit's
64-hex SHA-256 commit identities.

At request time, Phase202 uses the selected named remote's native map to translate
those SHA-256 boundaries to Git SHA-1 `shallow` lines. After pack import, native
`shallow-info` OIDs are translated back through the importer/native map before
`.pygit/shallow` is atomically replaced.

This keeps native SHA-1 confined to the smart-HTTP boundary.

## Have-set safety

Historical pygit `clone --depth` support marks a shallow boundary **after** a
full object transfer. That means objects behind the logical shallow boundary may
still physically exist in the object store/native map.

A Phase202 shallow fetch therefore deliberately sends no ordinary `have` lines.
The explicit `shallow` lines are the authoritative statement of history visible
to the server. This avoids accidentally advertising retained commits behind the
logical shallow boundary and defeating `--deepen` semantics.

The selected tips are still imported through the normal tag-preserving
SHA-1→SHA-256 importer, and returned `shallow-info` updates are applied inside
that same command so `--dry-run` restoration naturally discards them.

## Deliberate safety boundaries

Phase202 requires one named remote and does not combine shallow controls with:

- `--multiple`
- `--all`
- `--prefetch`
- `--refetch`
- explicit negotiation restrict/include controls
- `--negotiate-only`

These combinations need clearer cross-policy semantics before they should be
claimed as compatible.

`--deepen` and `--unshallow` require an existing shallow boundary.

## Important architectural limitation

Phase202 does **not** claim that the existing `clone --depth` has become a
bandwidth-saving shallow clone. pygit's SHA-256-native commit serialization
translates parent object IDs, so importing a commit whose unseen native parents
were omitted by a genuinely shallow initial pack requires a deeper object-model
solution than simply accepting a truncated pack.

The current clone path still downloads the complete native graph and then marks
a logical shallow boundary. Phase202 makes subsequent v2 depth changes protocol
correct for that model without pretending the initial transfer is already
optimized. A future phase can address true shallow initial import explicitly.

## Regression coverage

`tests/test_phase202.py` covers:

- `shallow`, `deepen`, and `deepen-relative` request framing
- required `fetch=shallow` capability validation
- shallow-info propagation on `V2FetchResult`
- SHA-256 shallow-file read/write/removal
- native SHA-1 → local SHA-256 shallow-info translation
- forced shallow exchange even when a selected tip is already known
- existing-shallow validation for deepen/unshallow
- infinite-depth unshallow framing
- one-shot shallow option forwarding through the Phase201 protocol adapter
- strict rejection of v0 fallback for shallow commands
- CLI stripping and `--` option-terminator behavior
- default named-remote activation
- protocol.version=2 requirement
- explicit rejection of Phase202-incompatible refetch composition
