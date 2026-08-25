# Phase 67: `pack-objects` plumbing

Phase 67 adds the producer-side counterpart to Phase 64 `index-pack` / `unpack-objects`: selected pygit objects can now be emitted as the repository's native educational SHA-256 pack format.

## Commands

```bash
printf '%s\n' <object-id> | pygit pack-objects objects/pack
printf '%s\n' HEAD | pygit pack-objects --revs objects/pack
printf '%s\n' HEAD ^v1 | pygit pack-objects --revs objects/pack
pygit pack-objects --all objects/pack </dev/null
printf '%s\n' HEAD | pygit pack-objects --revs --stdout >archive.pack
```

File mode writes a paired `<BASE-NAME>-<hash>.pack` and `.idx` using the existing `PackWriter`, then prints the 40-hex pack name suffix. `--stdout` writes only the binary pack stream and leaves no persistent temporary pack/index pair.

## Selection semantics

Without `--revs`, each non-empty stdin line resolves to exactly one object-ish expression. With `--revs`, positive revisions recursively include their object graph:

- commits include their root tree and, unless shallow, their parents;
- trees include every entry recursively;
- annotated tags include their target;
- blobs are leaves.

A line beginning with `^` subtracts the complete reachable closure of that revision. Negative revisions require `--revs` or `--all`.

`--all` adds every local ref plus `HEAD` as positive roots and performs recursive traversal. Unreachable/dangling objects are not included merely because they exist in the object database.

`.pygit/shallow` entries are respected: the shallow commit itself and its tree remain eligible, but its parents are not traversed.

## Python API

```python
from pathlib import Path
from pygit import pack_objects, reachable_objects, select_pack_objects

selected = select_pack_objects(repo, ["HEAD", "^v1"], revs=True)
result = pack_objects(
    repo,
    ["HEAD", "^v1"],
    revs=True,
    output_prefix=Path("objects/pack"),
)
print(result.pack_path, result.idx_path, result.object_count)

stream = pack_objects(repo, ["HEAD"], revs=True, stdout=True)
raw_pack = stream.pack_data
```

Structured results use `PackObjectsResult` with the deterministic selected OIDs, object count, pack-name hash, and either binary `pack_data` or persistent `.pack/.idx` paths.

## Compatibility boundary

This command deliberately emits pygit's own SHA-256, non-delta pack schema. It does not claim byte compatibility with native Git SHA-1/delta packfiles. The output is intended to round-trip through pygit's existing `PackReader`, `verify-pack`, `index-pack`, `unpack-objects`, and `fsck` plumbing.
