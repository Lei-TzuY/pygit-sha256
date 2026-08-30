# Phase276: trusted promisor size metadata

Phase276 connects Phase275's metadata-only protocol-v2 `object-info` size query to filtered-fetch promisor state. It persists only sizes that a supporting remote explicitly reports for objects already known to be unresolved promises.

The phase intentionally stops before changing `rev-list --filter=blob:limit` classification. This keeps persistence, transport fallback, and later filter semantics independently testable.

## Persistent state

`promisor.json` remains schema version 1. Phase276 adds an optional top-level `sizes` map:

```json
{
  "version": 1,
  "remotes": {},
  "promised": {"<native-sha1>": "blob"},
  "resolved": {},
  "sizes": {"<native-sha1>": 1234}
}
```

The field is deliberately additive rather than a version bump:

- old version-1 sidecars without `sizes` read as an empty size map;
- existing version-1 readers ignore unknown top-level JSON members;
- a size is stored only if the native OID is already an unresolved promise;
- unrelated/stale OIDs do not implicitly create promises;
- values must be non-negative integers;
- resolving a native promise removes its stale size metadata;
- `promised_size()` exposes only sizes belonging to still-unresolved promises.

No size is derived from `blob:limit`, tree metadata, a placeholder object, or local SHA-256 identity.

## Filtered-fetch enrichment

`PromisorFilteredNativeImporter` now exposes a read-only sorted tuple of the native OIDs it actually discovered as missing blob references.

After the filtered pack has imported successfully and those promises have been recorded, `fetch_partial` performs a best-effort metadata enrichment step:

1. collect the newly discovered unresolved native OIDs;
2. instantiate the Phase275 `SmartHttpV2ObjectInfoClient` for the same remote URL/timeout;
3. forward the exact effective protocol-v2 server options in their existing order;
4. request native uncompressed sizes without a pack fetch;
5. persist only non-`None` results for OIDs that remain unresolved promises.

The successful filtered fetch does **not** become dependent on `object-info`. Protocol-v0 fallback, a protocol-v2 server without the capability, malformed optional metadata, transport failure, or an unknown OID simply leaves the size absent. Existing later consumers therefore retain their strict fail-when-unclassifiable behavior.

## No content fallback

Size enrichment must never call the promisor materialization path merely to fill metadata. In particular it does not use `_fetch_native_object()` or `_fetch_native_objects()` and does not issue `command=fetch` through the Phase275 client.

This matters because content materialization would defeat the purpose of partial-clone filtering and would silently turn a metadata query into an object download.

## SHA-256-native boundary

Phase276 preserves the existing identity separation:

- `sizes` keys are native remote 40-hex SHA-1 OIDs belonging to unresolved promises;
- the values are scalar metadata only;
- no native SHA-1 is padded or promoted into a local 64-hex SHA-256 object slot;
- the local object database is unchanged by size enrichment;
- when a promise is eventually materialized, the normal importer writes a content-derived local SHA-256 object and removes the now-stale native size record.

## Failure semantics

`object-info` enrichment is optional after a successful filtered transfer. The enrichment helper therefore treats protocol/capability, response-validation, and transport failures as a metadata miss rather than failing the fetch command.

This is deliberately different from a future consumer that *requires* size in order to decide membership. Such a consumer may still fail before output when metadata is absent rather than guessing or downloading content.

## Tests

`tests/test_phase276.py` covers:

- reading legacy version-1 sidecars without `sizes`;
- storing size only for unresolved promises;
- cleaning size metadata when a promise resolves;
- rejecting negative, boolean, floating-point, and string sizes;
- exposing only actual importer-discovered native promises;
- end-to-end filtered-import enrichment with server-option preservation;
- explicit guards proving content materialization is not called;
- optional capability/query failure leaving the successful filtered fetch intact;
- remote `None`/unknown size results remaining absent.

The full Python 3.9/3.13 suite remains the final regression gate.

## Deferred work

A follow-up phase may teach general and ordered `blob:limit` selection to consume `promised_size()` metadata. That integration must separately define missing-object output/count semantics and must not place unresolved native SHA-1 identities into the local SHA-256 omission channel.
