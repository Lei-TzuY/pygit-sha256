# Phase 214 — protocol-v2 partial clone integration

Phase214 turns the Phase212 promisor object model and Phase213 lazy materializer into a usable `pygit clone --filter` path.

## CLI

`pygit clone` now accepts:

- `--filter=blob:none`
- `--filter=blob:limit=<positive-bytes>`

The split-value form `--filter <spec>` is also accepted by argparse. Ordered clone `--server-option` values are forwarded through the same protocol-v2 client used for ref discovery and the filtered fetch.

Phase214 intentionally rejects `--filter` combined with `--depth`. Partial clone and shallow clone are conceptually orthogonal in Git, but pygit currently has separate stable object models for omitted commit parents and omitted tree/blob children. Combining them safely requires a hybrid importer and is deferred rather than silently choosing one model.

## Transport

Filtered clone never calls the historical full `Repository.clone` fetch path.

The dedicated transport performs:

1. protocol-v2 `ls-refs`,
2. branch selection (`--single-branch` or all branch tips),
3. one filtered protocol-v2 fetch,
4. `PromisorFilteredNativeImporter` conversion,
5. conservative reachable-tag auto-follow,
6. native SHA-1 -> local SHA-256 map persistence,
7. branch/HEAD setup,
8. initial worktree materialization.

The remote must advertise protocol-v2 `fetch=filter`; no protocol-v0 fallback is permitted because dropping the filter would change the meaning and bandwidth profile of the command.

## Initial checkout and batching

A normal non-bare clone must populate the selected HEAD worktree. With `blob:none`, those file contents were intentionally omitted from the first pack.

Phase213 could already materialize one promised object lazily, but using that primitive directly during checkout would require one HTTP round-trip per missing file. Phase214 therefore adds `materialize_promised_objects()`:

- deduplicate native SHA-1 wants,
- keep already-resolved promises as metadata-only hits,
- validate every unresolved object against the single promisor remote,
- fetch all requested native objects in one protocol-v2 request without reapplying the repository filter,
- import them into the real SHA-256 object store,
- atomically move every resolved native oid out of `promised` and into `resolved`.

Before checkout, the selected HEAD tree is walked without resolving file entries. Only promised blobs reachable from that worktree are batched. Historical blobs remain promised and continue using Phase213's on-demand path when later commands need them.

This matches the useful semantics of partial clone: object filtering is independent of commit-history selection, while the currently checked-out snapshot still needs its file contents.

## Stable SHA-256 identity

Phase212 foreign trees continue to serialize original native Git SHA-1 child identities using `pygit-native-tree-v1`. Runtime local SHA-256 resolutions are ephemeral metadata.

Batching a checkout blob therefore does not rewrite:

- the foreign tree payload,
- the tree's SHA-256 id,
- any commit referencing that tree.

No synthetic or surrogate SHA-256 ids are introduced.

## Repository metadata

A filtered clone persists:

- `protocol.version=2`,
- `remote.origin.promisor=true`,
- `remote.origin.partialCloneFilter=<filter-spec>`,
- `.pygit/promisor.json`,
- the ordinary remote fetch/tracking metadata finalized by the existing clone frontend.

The partial-clone filter therefore survives the initial command and the promisor remote remains identifiable for future lazy materialization.

## Tags

Phase214 auto-follows tags only when their peeled target is already in the selected imported history. Lightweight tags can be installed immediately. Missing annotated tag objects are fetched with the same filter and server options. This avoids pulling unrelated history just because a remote advertises an unrelated tag.

## Compatibility

No-filter clone keeps the Phase209 path unchanged. Shallow clone keeps the Phase204/206 path unchanged. Fetch-side filter/update-shallow behavior remains owned by Phases212 and earlier.

Phase214 does not yet add:

- `--filter` + `--depth` hybrid import,
- `--no-checkout`,
- sparse checkout,
- multiple promisor remotes,
- submodule filtering,
- `--filter=auto`.
