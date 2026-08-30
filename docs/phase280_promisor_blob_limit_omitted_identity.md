# Phase280: promisor `blob:limit` omission identity

Phase280 composes Phase278's trusted promisor-size classification with the existing ordered `rev-list --filter=blob:limit=... --filter-print-omitted` presentation stack.

## Problem

An unresolved promised blob has a remote-native object identity but does not yet have a repository-visible local SHA-256 object id. Phase278 made its trusted uncompressed size available for filter membership, which creates two distinct cases when omission printing is also requested.

1. A promised blob whose size is below the threshold survives `blob:limit`. It is still missing locally and must remain in the existing missing-object channel.
2. A promised blob whose size is at or above the threshold is omitted by the filter. Git's omission channel requires an object id prefixed by `~`, but pygit cannot legally substitute the remote-native SHA-1 for a local SHA-256 identity.

## Behavior

Phase280 keeps those channels separate.

- Trusted-size promised blobs smaller than the threshold remain in the ordered inventory.
- `--missing=allow-promisor` tolerates such entries silently.
- `--missing=print` reports them through the existing `?native-oid` channel.
- `--missing=print-info` preserves path/type metadata.
- Structured `-z` output keeps the established `missing=yes` record format.
- `--count` continues to count present local objects only; surviving missing records are emitted before the final newline count.

If trusted size says an unresolved promised blob is filtered, `--filter-print-omitted` now fails before any output with an explicit identity-boundary error. The user may materialize the blob first, creating its genuine content-derived local SHA-256, or omit `--filter-print-omitted`.

## Why no `~<native-sha1>`

The remote-native 40-hex SHA-1 belongs to transport/promisor metadata. The repository-visible object domain is SHA-256. Emitting the native SHA-1 in the omission channel would silently collapse those domains; padding or translating it would invent an object identity that does not exist.

Phase280 therefore preserves the invariant that every `~<oid>` produced by pygit is a genuine local 64-hex SHA-256.

## Git compatibility

Current `git rev-list` documents these as independent protocols:

- `blob:limit=<n>[kmg]` omits blobs whose size is at least the threshold;
- `--filter-print-omitted` prints filter omissions with a `~` prefix;
- `--missing=print` prints missing objects with a `?` prefix;
- `--missing=print-info` adds inferred metadata for missing objects.

Pygit follows those observable roles while retaining its stricter cross-hash-domain boundary for foreign partial-clone promises.

## No materialization side effect

Classification uses only trusted Phase276/278 size metadata. The omission path never calls single-object or batch promisor content fetch merely to obtain an omission id or decide membership.

## Tests

`tests/test_phase280.py` covers:

- known-small promises staying in `--missing=print`;
- `print-info` path/type preservation;
- structured NUL missing records;
- missing-before-count ordering;
- silent `allow-promisor` behavior;
- exact-threshold filtered promises failing before output because no local SHA-256 exists;
- the same identity refusal under NUL/count framing;
- retained strict failure when trusted size metadata is absent;
- explicit zero-content-fetch guards and unchanged promisor state.
