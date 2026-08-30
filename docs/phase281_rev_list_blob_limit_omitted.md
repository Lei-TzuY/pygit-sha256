# Phase281: plain `rev-list blob:limit` omitted output

Phase281 adds non-`--in-commit-order` line/count parity for `rev-list --filter=blob:limit=<n>[kmg] --filter-print-omitted` by composing the existing Phase278 blob-size classifier with Git's established omission/missing/count renderer ordering.

## Scope

Supported in this phase:

- `--objects --filter=blob:limit=... --filter-print-omitted`
- `--missing=allow-promisor`
- `--missing=print`
- `--missing=print-info`
- `--count`
- existing line-oriented boundary/object-edge selection handled by the underlying metadata inventory

Plain non-ordered `blob:limit -z` remains explicitly deferred. Ordered NUL omission framing is already owned by the Phase269/270/280 stack.

## Output ordering

The existing omission renderer defines the line protocol used here:

1. surviving traversal records;
2. genuine local `~<sha256>` filter omissions;
3. missing-object diagnostics;
4. optional final count.

Phase281 does not add a new walker. It derives the omitted local blob set from the same promisor-aware inventory used by the blob-limit classifier, captures the already-filtered traversal, and feeds those two results through the established ordering.

## Threshold semantics

A blob is kept only when its uncompressed size is strictly less than the threshold. A size equal to the threshold is omitted, matching current Git documentation.

For materialized local blobs, size comes from the local `BlobObject`. For unresolved promises, Phase278's trusted `promised_size()` metadata is used without fetching content.

## Promisor identity boundary

A trusted-size unresolved promise below the threshold survives the filter and remains a missing entry:

- `allow-promisor` tolerates it silently;
- `print` emits the existing `?<native-oid>` record;
- `print-info` preserves path/type metadata.

A trusted-size unresolved promise at or above the threshold is known to be filter-omitted, but it still has no local SHA-256. Because `--filter-print-omitted` requires an object identity in the omission channel, pygit refuses this case before any output. It never emits the remote-native 40-hex SHA-1 as `~<oid>`, never pads/translates it, and never downloads content merely to manufacture an omission identity.

Missing trusted size metadata retains the Phase278 strict preflight error.

## Router composition

`try_run_rev_list_blob_limit_filter_print_omitted()` is routed immediately before the ordinary blob-limit handler. It declines `--in-commit-order`, leaving the Phase280 ordered adapter authoritative, and declines all non-blob-limit requests so existing `blob:none` / `object:type` omission handlers remain unchanged.

## Verification

`tests/test_phase281.py` covers:

- exact-threshold local omission and genuine 64-hex SHA-256 output;
- omission-before-count framing;
- trusted-size promised blobs surviving in `print` and `print-info` missing channels;
- filtered promises failing before output due to missing local SHA-256 identity;
- retained no-size preflight failure;
- explicit zero-content-fetch guards and unchanged promisor state;
- retained plain non-ordered `-z` deferral.
