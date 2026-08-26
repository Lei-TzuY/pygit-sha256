# Phase 141 — rev-list on-disk storage accounting

Phase 141 adds Git-style `rev-list --disk-usage` and `--disk-usage=human` on top of the existing revision selector and object enumeration stack.

## Commands

```bash
pygit rev-list --disk-usage HEAD
pygit rev-list --disk-usage --objects --all
pygit rev-list --disk-usage=human --objects HEAD
pygit rev-list --disk-usage --boundary release..HEAD
pygit rev-list --disk-usage --objects-edge release..HEAD
```

Normal commit/object output is suppressed and replaced by the sum of bytes used by the selected commits or objects in the repository's visible object storage. Without `--objects`, only selected commit objects contribute. With `--objects` or `--objects-edge`, the selected tree/blob closure contributes as well.

## Storage semantics

Accounting reuses `cat_file.object_disk_size()` rather than estimating from uncompressed object payloads. Loose objects therefore use their compressed loose-file size, while packed objects use the exact encoded pack-entry width. The same primary-then-alternate object-database lookup policy as ordinary object reads is preserved.

This also means packed-only objects are measured without materializing loose copies. Duplicate visible copies are handled by the existing object-store precedence rules rather than double-counted.

## Boundary and edge records

`--boundary` commits are part of the displayed revision stream and therefore contribute to the byte total, matching native Git.

`--objects-edge` is different: excluded edge commits remain advertised as leading `-OID` records, but they do not contribute to the disk-usage total. The adapter preserves those edge lines and sums only the selected object stream that follows them.

## Formatting composition

Presentation-only modes such as `--header`, `--timestamp`, `--parents`, `--children`, and object pathname decoration do not alter storage accounting. They are intentionally suppressed while the selected OIDs are measured.

Current Git emits a zero count followed by the storage total when `--count` and `--disk-usage` are combined. Phase 141 preserves that observable plumbing behavior.

`--disk-usage=human` reuses pygit's existing binary-unit formatter, producing values such as `579 bytes`, `12.24 KiB`, or `3.50 MiB`.

## Safety

The feature is read-only. It does not rewrite packs, create loose objects, change refs or reflogs, touch the index, or modify the worktree.

Bitmap acceleration, promisor/missing-object accounting, and multi-copy physical-storage aggregation remain separate concerns. The reported value intentionally follows the same visible-copy semantics as `cat-file %(objectsize:disk)` rather than summing every redundant physical copy of an object.
