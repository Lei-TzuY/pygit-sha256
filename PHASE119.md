# Phase 119 — exact raw object reads

Phase 119 makes object inspection byte-faithful across loose and packed storage and fixes `cat-file` batch contents that previously depended on re-serializing the in-memory object model.

## Problem

`cat-file.inspect_object()` used `ObjectStore.read()` and then `obj.serialize()`. That is correct only when the Python object model represents every stored header. Commit parsing intentionally ignores headers it does not model, such as embedded `gpgsig` blocks or future extension headers, so re-serialization could silently drop bytes and report the wrong payload size. The problem applied to both loose and packed objects and was especially visible for imported commit objects.

The pack reader also had no public way to return a validated object envelope without immediately converting it into a `GitObject`.

## Storage-neutral raw API

`PackReader.read_store_bytes(oid)` now returns the exact validated `<type> <size>\0<payload>` bytes from a pack entry. It reuses the same strict validation as ordinary packed reads: pack checksum, index offsets, zlib boundaries, CRC-32, canonical envelope, object type, and SHA-256 identity are checked before bytes are exposed. `read_object()` now parses the result of that primitive rather than duplicating the decoding path.

`ObjectStore.read_store_bytes(oid)` provides the corresponding loose-or-packed API. It preserves the existing lookup rules: a loose object wins; MIDX-selected packs are preferred; a damaged selected packed copy may fall back to another valid copy; and packs not yet covered by a stale MIDX remain visible. Returned bytes are additionally parsed once for normal pygit object readability, but they are never re-serialized.

Raw reads are observational: reading a packed-only object does not recreate a loose copy.

## `cat-file` byte fidelity

`inspect_object()` now derives `type_name`, `size`, and `content` directly from the validated stored envelope. As a result:

- `cat-file --batch` contents preserve headers unknown to the in-memory model;
- `cat-file --batch-command` `contents` responses preserve the same exact payload;
- `%(objectsize)` / default object-size metadata describes the stored payload, not a reconstructed approximation;
- loose and packed copies produce identical logical payload bytes.

Protocol framing is unchanged. Newline/NUL framing, custom batch formats, `--batch-all-objects`, buffering, and Phase 116 symlink traversal continue to compose with the same `CatFileRecord` interface.

## Compatibility boundary

No object, pack, index, or MIDX format changes are introduced. The educational SHA-256/non-delta compatibility boundary remains unchanged. Ordinary parsed-object APIs still return the same `GitObject` classes; Phase 119 adds an exact envelope path for callers that require byte fidelity.

Single-object pretty-print behavior is unchanged; this phase is specifically about stored-object metadata and raw batch contents.

## Regression coverage

`tests/test_phase119.py` uses a valid commit carrying `gpgsig` and an extra unmodeled header to prove the original loss mechanism. It covers exact loose envelopes, exact packed envelopes, packed-only reads without loose materialization, byte-faithful ordinary batch contents, byte-faithful batch-command contents, and the pack reader's missing-object result.
