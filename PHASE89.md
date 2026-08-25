# Phase 89 — `cat-file --batch-all-objects`

Phase 89 extends the Phase 55/82/84 batch inspector from stdin-selected object expressions to complete local object-store enumeration.

## Semantics

- `--batch-check --batch-all-objects` emits metadata for every known object.
- `--batch --batch-all-objects` emits metadata plus raw object contents.
- `--batch-command --batch-all-objects` emits metadata for all objects and ignores command stdin.
- stdin is never consumed in all-object mode.
- custom batch formats continue to work; `%(rest)` is empty because enumeration has no stdin record.
- `--buffer` remains valid.

Enumeration is storage-based rather than reachability-based. It includes loose, packed-only, reachable, and unreachable objects. Loose/packed duplicates are deduplicated and output is sorted lexically by canonical 64-hex SHA-256 object ID.

Incidental non-object files under `.pygit/objects` are ignored. Object data is still read through the normal verified object store; discovery does not bypass pack, hash, type, or payload validation.

## API

- `all_object_ids(repo) -> tuple[str, ...]`
- `batch_all_objects(repo, *, contents=False, format_string=None)`

## Scope boundary

`--unordered` remains separate because a useful implementation should expose storage-native traversal rather than merely aliasing the deterministic order. Symlink following, disk-size/delta-base atoms, filters/textconv, mailmap behavior, and NUL framing also remain separate work.
