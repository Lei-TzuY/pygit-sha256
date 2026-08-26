# Phase 95 — `cat-file --unordered`

Phase 95 adds storage-local all-object enumeration for large batch scans while preserving the existing deterministic hash-order default.

## CLI

```bash
pygit cat-file --batch-check --batch-all-objects --unordered
pygit cat-file --batch --batch-all-objects --unordered --buffer
pygit cat-file --batch-check='%(objectname) %(objectsize:disk)' --batch-all-objects --unordered -Z
```

`--unordered` is valid only with `--batch-all-objects`. Without it, the existing hash-sorted output remains unchanged.

## Enumeration strategy

The unordered path deliberately avoids `ObjectStore.all_shas()`, which materializes a complete set and globally sorts every object ID. Instead it streams storage in locality-oriented groups:

1. canonical loose-object files are yielded directly while walking loose storage;
2. pack files are visited in deterministic path order;
3. entries within a pack are visited by physical pack offset.

A `seen` set is retained because Git's contract still requires each object to appear only once even when the same object has loose and packed copies or occurs in multiple packs. The output order itself is intentionally unspecified and callers must not depend on it.

This mirrors native Git's `--unordered` contract: when `--batch-all-objects` is active, object visitation may use a more efficient order than hash order, especially for content-heavy batch reads, while still emitting each object once.

## Compatibility

The option composes with the existing batch response modes, custom formats including `%(objectsize:disk)` and `%(rest)`, `--buffer`, and `-Z` framing. Raw object content is passed through unchanged.

`--unordered` changes enumeration order only. It does not change object selection, reachability, lookup precedence, validation, formatting, or missing-object behavior.

## Regression coverage

`tests/test_phase95.py` verifies:

- ordered iteration still matches the existing hash-sorted enumeration;
- unordered enumeration bypasses the global sorted enumerator;
- loose objects are grouped before packed-only objects and duplicates are suppressed;
- incidental noncanonical loose files are ignored;
- CLI grammar rejects `--unordered` without `--batch-all-objects`;
- all-object batch output emits each object exactly once;
- custom formatting, `%(objectsize:disk)`, NUL framing, and binary contents remain intact.
