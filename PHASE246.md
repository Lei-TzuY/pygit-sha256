# Phase 246 — metadata-only rev-list blob filtering

Phase246 adds `--filter=blob:none` to the existing promisor-aware `rev-list --missing` traversal.

Supported forms include `--objects --filter=blob:none` with `--missing=allow-promisor`, `print`, or `print-info`, including the already-supported edge and boundary presentation paths.

Git defines `blob:none` as omitting blobs from object traversal. This phase preserves that behavior without materializing omitted promisor blobs. Present commits and trees keep their real local SHA-256 identities; unresolved foreign SHA-1 identities remain confined to promisor metadata and disappear from output when their blobs are filtered.

`--count` and `-z` remain deliberately unsupported with this filter until their filtered framing semantics are modeled directly.

Focused tests cover allow-promisor, print-info, edge/boundary composition, zero materialization, unchanged promisor state, and explicit rejection of unmodeled combinations.
