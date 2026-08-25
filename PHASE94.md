# Phase 94 — `cat-file %(objectsize:disk)`

Phase 94 adds storage-footprint reporting to the batch `cat-file` format language. `%(objectsize)` remains the logical uncompressed object payload size; `%(objectsize:disk)` reports the bytes occupied by the selected stored copy.

## CLI

```bash
printf 'HEAD\n' | pygit cat-file --batch-check='%(objectname) %(objectsize) %(objectsize:disk)'
pygit cat-file --batch-check='%(objectname) %(objectsize:disk)' --batch-all-objects
printf 'HEAD\0' | pygit cat-file --batch-check='%(objectsize:disk)' -Z
```

The atom composes with `--batch`, `--batch-check`, `--batch-command`, custom formats, `--batch-all-objects`, buffering, and Phase 93 NUL framing without changing default batch output.

## Storage semantics

For a loose object, the reported disk size is the exact compressed loose-object file length.

For a packed object, the reported disk size is the exact encoded entry width inside the `.pack` payload: the pack entry header plus compressed bytes, excluding neighboring entries and the final pack checksum. Boundaries are derived from the validated pack index and validated pack envelope.

If the same object exists both loose and packed, pygit follows the same lookup preference as normal object reads and reports the loose copy. If multiple packed copies exist, one valid copy is selected deterministically. As with native Git, callers should not attribute shared repository disk usage to refs solely from per-object disk sizes because duplicate copies and repacking can change which representation is selected.

## Python API

`inspect_object()` now exposes `CatFileRecord.disk_size`, and `object_disk_size(repo, oid)` provides a reusable direct query for a resolved SHA-256 object ID.

## Regression coverage

`tests/test_phase94.py` locks loose compressed sizes, exact packed-entry sizes, loose-over-packed duplicate preference, custom format expansion, canonical missing records, `-Z` framing, and `--batch-all-objects` composition.
