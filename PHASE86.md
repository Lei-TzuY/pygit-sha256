# Phase 86 — `cat-file --batch-all-objects`

Phase 86 extends the Phase 55/82/84 batch object inspector from stdin-selected object expressions to complete local object-store enumeration.

## Added behavior

- `pygit cat-file --batch-check --batch-all-objects` emits metadata for every known object.
- `pygit cat-file --batch --batch-all-objects` emits each header followed by raw serialized object contents.
- `pygit cat-file --batch-command --batch-all-objects` follows Git's observed all-object behavior and emits metadata for all objects while ignoring command stdin.
- stdin is never consumed for all-object enumeration.
- custom Phase 84 batch formats remain supported; `%(rest)` expands to an empty string because no input record exists.
- `--buffer` remains accepted and flushes the completed enumeration stream.

## Storage semantics

Enumeration is storage-based, not graph-based. It therefore includes reachable and unreachable objects, loose objects, packed-only objects, and objects duplicated between loose and packed storage.

`all_object_ids()` returns canonical lowercase 64-hex SHA-256 IDs in deterministic lexical order. Loose/packed duplicates are deduplicated. Incidental files whose paths happen to be beneath a two-character loose-object directory but whose combined name is not a canonical SHA-256 ID are ignored.

Object data is still read through the ordinary verified object store. A canonical object filename does not bypass hash/type validation merely because it was discovered by enumeration.

## Public API

Phase 86 exports:

- `all_object_ids(repo) -> tuple[str, ...]`
- `batch_all_objects(repo, *, contents=False, format_string=None)`

The second helper composes existing Phase 84 formatting and Phase 55 binary-content semantics rather than maintaining a second response renderer.

## Verification coverage

`tests/test_phase86.py` covers:

- deterministic sorting and loose/pack deduplication;
- packed-only objects;
- unreachable object enumeration;
- rejection of noncanonical loose-object filenames;
- canonical corrupt-object missing behavior;
- custom formats and empty `%(rest)`;
- binary `--batch` contents;
- stdin ignoring for `--batch-check` and `--batch-command`;
- `--buffer` composition;
- invalid mode/positional combinations;
- clean behavior for an empty object store.

## Scope boundary

`--unordered` remains intentionally separate. Its value is storage-native traversal/optimization rather than another spelling of deterministic sorting, so it should be implemented only when the object store exposes a meaningful unordered traversal contract. Symlink following, disk-size/delta-base atoms, text conversion/filters, mailmap handling, and NUL framing also remain separate work.
