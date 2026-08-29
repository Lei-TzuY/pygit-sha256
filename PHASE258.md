# Phase 258 — metadata-only `rev-list --filter=blob:limit=<n>[kmg]`

Phase258 extends the SHA-256-native, promisor-aware `rev-list --objects*` filter stack with Git's size-limited blob filter.

## Compatibility target

Git 2.55 documents `blob:limit=<n>[kmg]` as omitting blobs whose uncompressed payload size is **at least** the requested threshold. `k`, `m`, and `g` are binary units, so `1k` equals `1024` bytes. A threshold of zero therefore omits every blob.

This phase supports line-oriented object traversal and structured `--count` with the existing metadata-only missing modes:

- `--missing=allow-promisor`
- `--missing=print`
- `--missing=print-info`
- `--boundary`
- `--objects-edge`
- `--count`

`-z` and `--filter-print-omitted` are deliberately deferred so their framing can be composed explicitly rather than inferred from line-oriented output.

## SHA-256-native and promisor boundary

For a present local blob, pygit reads only the already-materialized local SHA-256 object and uses `len(blob)` as the Git object payload size. Kept objects therefore continue to expose genuine local 64-hex SHA-256 identities.

An unresolved foreign promise currently records native identity and kind, but not blob size. Phase258 refuses such a `blob:limit` traversal with a clear error instead of fetching the blob merely to classify it. This preserves the metadata-only contract, keeps promisor state unchanged, and avoids inventing a local SHA-256 for content that has not been materialized.

## Count semantics

The count path is computed from structured inventory rather than from rendered lines. Present non-blobs always survive; present blobs survive only when `size < limit`; missing objects never contribute to the numeric count. Existing edge/boundary overlap handling remains authoritative.

## Tests

Focused coverage verifies:

- exact-threshold exclusion (`size >= limit`)
- `1k == 1024` binary-unit parsing
- zero threshold omitting all blobs
- filtered present-object counts
- unresolved promised blobs fail without single-object or batch fetches
- promisor metadata remains unchanged after refusal
- invalid size syntax is rejected
- deferred NUL and omitted-object combinations are rejected explicitly
