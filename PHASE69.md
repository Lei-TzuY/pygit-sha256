# Phase 69: strict pack-index inspection

Phase 69 adds a single validated parser for pygit's SHA-256 fan-out `.idx` files and exposes it through `show-index`.

## CLI

```bash
pygit show-index .pygit/objects/pack/pack-....idx
pygit show-index --verbose .pygit/objects/pack/pack-....idx
pygit show-index --count < pack.idx
pygit show-index < pack.idx
```

Default output is one `OFFSET OID` record per indexed object. `--verbose` appends the stored CRC32 as eight lowercase hexadecimal digits. With no file argument, the raw index image is read from stdin, so the command can be used in pipelines and does not require a repository.

## Validation boundary

`parse_index_bytes()` / `parse_index()` reject malformed indexes before returning any records. Validation covers:

- the `\xfftOc` signature and version 2 header;
- a monotonic 256-entry cumulative fan-out table;
- exact file length derived from the final fan-out count;
- SHA-256 checksum of the entire index payload;
- canonical lowercase 64-hex SHA-256 object IDs;
- strictly increasing, duplicate-free OID ordering;
- exact agreement between fan-out buckets and the OID table;
- object offsets that do not point before the 12-byte pack header and are not duplicated.

The existing `PackReader` now consumes this same parser. A corrupt `.idx` therefore fails loudly instead of being interpreted as an empty or partially readable index.

## Python API

```python
from pygit import PackIndexEntry, ParsedPackIndex, parse_index, parse_index_bytes

index = parse_index(path)
for entry in index.entries:
    print(entry.offset, entry.oid, f"{entry.crc32:08x}")
```

`ParsedPackIndex` also exposes the validated fan-out table, index checksum, version, and `object_count`.

## Compatibility boundary

This remains pygit's own SHA-256 pack-index format: object names are stored as 64 ASCII hex characters and offsets are currently 32-bit. Phase 69 does not claim byte compatibility with native Git's SHA-1/SHA-256 index encodings, large-offset table, reverse indexes, multi-pack indexes, or bitmap indexes.
