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

Both temporary files exist and are durable before step 1 begins. If the second rename fails synchronously, the newly published pack is removed and remaining temporary files are cleaned up, so the failed call leaves no new final pair.

This is a two-file protocol rather than a claim of filesystem-wide transactional rename. An abrupt process or machine failure between the two final renames may leave a pack-only orphan. That state is intentionally non-discoverable by normal index-driven lookup, and a later identical write recognizes the matching content-derived orphan and installs only the missing index.

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

`tests/test_phase117.py` covers normal readable output, idempotent complete-pair writes, `fsync` failure before publication, rollback when index publication fails, recovery from matching pack-only and index-only orphans, collision rejection, and temporary-file cleanup.
