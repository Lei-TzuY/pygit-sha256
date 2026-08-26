# Phase 104 — SHA-256 multi-pack-index

Phase 104 adds a shared index across pygit's packfiles and wires it into the object-store read path. Repositories with several packs can now resolve a packed SHA-256 object to one source pack without opening every per-pack `.idx` file on each lookup.

## CLI

```bash
pygit multi-pack-index write
pygit multi-pack-index verify
```

`write` scans the current `.pygit/objects/pack/*.idx` files, validates every source index with the existing strict pack-index parser, requires the paired `.pack` file, and atomically replaces `.pygit/objects/pack/multi-pack-index`.

`verify` validates both the MIDX binary image and its relationship to the current source indexes. It checks that the MIDX object set exactly equals the union of the named `.idx` files and that every selected object-to-pack offset still agrees with its source index. Full compressed-object, CRC and pack-trailer validation remains the responsibility of Phase 102 `verify-pack`; MIDX verification does not duplicate that parser.

## Binary format

The format is deliberately Git-inspired while preserving pygit's SHA-256-native educational storage model:

```text
MIDX header
chunk lookup table
PNAM  sorted .idx basenames, NUL terminated/padded
OIDF  256-entry cumulative fan-out table
OIDL  sorted raw 32-byte SHA-256 object names
OOFF  (pack-id, 32-bit pack offset) records
SHA-256 trailer
```

The header is version 1 with hash-version 2 (SHA-256), four chunks, no base MIDX files, and a 32-bit pack count. The chunk lookup table uses 4-byte chunk IDs and 64-bit offsets. Current pygit pack indexes use 32-bit object offsets, so Phase 104 intentionally does not invent a large-offset chunk that the underlying pack format cannot consume.

Parsing is strict: signature/version/hash version, chunk ordering and bounds, canonical pack names and padding, fan-out monotonicity/exactness, sorted object IDs, pack IDs, offsets, exact chunk sizes, and the SHA-256 trailer are all validated before an image is accepted.

## Duplicate objects

The same object may legitimately appear in more than one pack. The MIDX stores one mapping per object ID and deterministically selects the lexicographically first source `.idx` basename. The object identity itself remains content-addressed, so any valid source copy is equivalent.

The selected MIDX copy is a fast path, not a single point of failure. If that pack is missing or corrupt, `ObjectStore.read()` remembers the storage error and searches the remaining indexes for an independently valid copy of the same object. A valid duplicate is returned; the recorded error is raised only when no usable copy remains. This preserves the repository's pre-existing redundant-pack behavior regardless of directory iteration order.

## Object-store integration

`ObjectStore.read()` and `ObjectStore.exists()` now consult the MIDX after checking loose storage. A normal hit chooses the recorded pack directly instead of linearly parsing all covered `.idx` files. MIDX lookup itself is an O(log N) binary search compatible with the Python 3.9 test floor. `all_shas()` enumerates MIDX-covered object IDs without reopening those indexes, and `resolve_prefix()` now resolves abbreviations across loose and packed storage rather than only loose-object directories.

A MIDX is an accelerator rather than a requirement. If a new pack appears after the MIDX was written, its `.idx` basename is not in the MIDX pack-name set, so the object store still scans that uncovered index. This gives a safe stale-index transition until the next `multi-pack-index write`. A malformed MIDX itself still fails strict parsing; failures in a MIDX-selected source pack are surfaced unless another valid redundant copy can satisfy the read.

## Regression coverage

`tests/test_phase104.py` covers:

- writing/parsing multiple packs and object lookup;
- deterministic duplicate-object selection;
- checksum and invalid-pack-ID corruption;
- verification against source indexes and missing pack pairs;
- MIDX fast-path reads without scanning unrelated covered indexes;
- fallback from a corrupt MIDX-selected copy to a redundant valid pack;
- fallback to packs created after the MIDX;
- packed-object `all_shas()` and abbreviation resolution;
- installed `multi-pack-index write` / `verify` routing and help/error behavior.
