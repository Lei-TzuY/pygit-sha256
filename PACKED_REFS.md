# Packed references

Phase 54 adds Git-style packed-reference storage to pygit's native SHA-256 ref backend.

## Why packed refs exist

Loose refs are easy to inspect: each branch or tag is a small file below `.pygit/refs/`. A repository with many refs eventually spends more filesystem metadata and directory traversal work on those tiny files than necessary. `packed-refs` consolidates direct refs into one sorted text file while keeping loose refs as an override layer.

pygit now follows the same core precedence rule as Git:

1. a loose ref file wins when both loose and packed copies exist;
2. if no loose file exists, the packed value is used;
3. symbolic refs stay loose and are never packed;
4. deleting a ref removes both representations so an older packed value cannot reappear.

## File format

`.pygit/packed-refs` uses 64-hex SHA-256 object IDs:

```text
# pack-refs with: peeled fully-peeled sorted
0123...cdef refs/heads/main
4567...89ab refs/tags/v2
^89ab...0123
```

A line beginning with `^` is the fully peeled target of the annotated tag on the immediately preceding line. The parser rejects malformed object IDs, invalid ref names, duplicate refs, duplicate peeled lines, and orphan peeled lines.

## CLI

Pack tags, which is the conservative default:

```bash
pygit pack-refs
```

Pack every direct ref below `refs/`:

```bash
pygit pack-refs --all
```

Write packed entries but keep the loose files:

```bash
pygit pack-refs --all --no-prune
```

The command is silent on success.

## Transparent readers

After packing, callers do not need a special code path. These operations read loose and packed refs through the same backend:

```bash
pygit show-ref
pygit show-ref --verify refs/tags/v2
pygit for-each-ref
pygit name-rev HEAD~2
pygit merge-base main topic
```

`HEAD` can continue pointing at `refs/heads/main` even when that branch exists only in `packed-refs`.

The `RefStore` branch, tag, remote-tracking, stash, listing, and generic resolution helpers also consult packed storage.

## Updates and deletion

`update-ref` keeps Git's loose-shadow behavior. Updating a packed branch creates a new loose ref and leaves the older packed value underneath until the next `pack-refs` run:

```bash
pygit update-ref refs/heads/main <new> <old>
```

The visible value is immediately `<new>` because loose refs take precedence.

Deletion is different: both loose and packed copies are removed atomically from the transaction's point of view so the packed value cannot become visible again:

```bash
pygit update-ref -d refs/heads/topic <old>
```

Compare-and-swap checks work against packed-only refs exactly as they do against loose refs.

## Python API

```python
from pygit import PackedRef, pack_refs, read_packed_refs

packed = pack_refs(repo, all_refs=True)
records = read_packed_refs(repo.pygit_dir)
print(records["refs/heads/main"].oid)
```

`pack_refs(repo, all_refs=False, prune=True)` returns the refs newly packed from loose storage. Existing packed records are preserved unless a loose ref of the same name replaces their value.

## Safety properties

Phase 54 regression coverage verifies:

- default tag-only packing;
- `--all` and `--no-prune`;
- loose-over-packed precedence;
- packed-only `HEAD` resolution;
- annotated-tag peeled lines;
- `show-ref`, `for-each-ref`, and `name-rev` visibility;
- packed-ref compare-and-swap updates;
- deletion without packed-value resurrection;
- symbolic refs remaining loose;
- remote namespace rename/delete across packed storage;
- strict malformed-file rejection.

The packed file is replaced with an atomic filesystem rename. When pruning loose refs, pygit snapshots the original packed file and loose ref bytes and restores them if a filesystem error interrupts publication.
