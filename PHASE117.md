# Phase 117 — failure-safe pack/index publication

Phase 117 hardens the shared `PackWriter` so commands that write pack files directly no longer stream bytes into authoritative `.pack` / `.idx` paths.

## Problem

`PackWriter.write_pack_and_idx()` previously wrote the generated pack and index with two direct `Path.write_bytes()` calls. A short write, `fsync`-equivalent storage failure, or exception between the two writes could expose a truncated final file or a newly visible index whose sibling pack was incomplete.

This mattered beyond low-level tests: `pack-objects` file mode writes through `PackWriter` directly, while repack and multi-pack-index maintenance also depend on the same producer even when they add higher-level staging of their own.

## Staged publication

The writer now builds both complete byte images in memory, then stages every missing final file in a hidden same-directory temporary file. Each temporary file is fully written, flushed, and `fsync`ed before publication.

For a new pair, publication order is deliberately:

1. publish the `.pack` with `os.replace()`;
2. publish the `.idx` last as the discovery/commit point.

Both temporary files exist and are durable before step 1 begins. If index publication fails, the already-published matching pack is intentionally retained as a pack-only orphan. The index is the discovery point, so that orphan is not advertised by normal index-driven lookup and a later identical write can install only the missing index.

The writer deliberately does **not** unlink the final pack as rollback after an index rename failure. Final pack paths are shared immutable publication targets: without an ownership token, a losing concurrent writer cannot prove that the current final pack still belongs exclusively to it. Deleting that path could remove the pack just committed by another identical writer and leave a visible `.idx` without its sibling pack.

This is a two-file protocol rather than a claim of filesystem-wide transactional rename. An abrupt process or machine failure between the two final renames may likewise leave a pack-only orphan; the recovery rule is the same.

## Immutable target and recovery rules

Pack basenames are derived from pack content. Phase 117 therefore treats existing final paths as immutable:

- a complete matching pair is an idempotent no-op;
- a matching pack-only or index-only orphan is completed without rewriting the matching file;
- an existing final path with different bytes is rejected as a target collision before any temporary file is created.

These rules avoid silently overwriting unrelated data while making interrupted identical publications self-healing.

## Compatibility boundary

The educational SHA-256 pack and fan-out index formats are unchanged. Object ordering, pack checksums, index checksums, CRCs, offsets, filenames, and `PackReader` behavior remain byte-for-byte compatible with the previous writer for the same input object set.

Phase 117 changes only how the already-generated pair is installed on disk.

## Regression coverage

`tests/test_phase117.py` covers normal readable output, idempotent complete-pair writes, `fsync` failure before publication, safe pack-only retention when index publication fails, deterministic concurrent-writer interleaving, recovery from matching pack-only and index-only orphans, collision rejection, and temporary-file cleanup.
