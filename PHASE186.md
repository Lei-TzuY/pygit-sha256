# Phase 186: FETCH_HEAD revision integration

Phase 186 makes the `FETCH_HEAD` metadata introduced by Phase 184 usable as a Git-style pseudo-ref throughout pygit's revision machinery.

## Behavior

After a fetch writes `.pygit/FETCH_HEAD`, the token `FETCH_HEAD` resolves to the first object ID recorded in that file. This mirrors native Git's revision behavior and intentionally does not interpret the `not-for-merge` marker while resolving the pseudo-ref: mergeability is metadata for pull/merge selection, while revision lookup names the first recorded object.

Because `FETCH_HEAD` now participates in the existing unified revision resolver, normal revision operators compose automatically, including examples such as:

```text
FETCH_HEAD
FETCH_HEAD^
FETCH_HEAD~2
FETCH_HEAD^{commit}
FETCH_HEAD:path/to/file
```

Missing or empty `FETCH_HEAD` remains unresolved. Malformed metadata is rejected explicitly. A syntactically valid SHA-256 object ID that is no longer present in the object store can still be read by the low-level ref layer, while the unified revision layer rejects it as an unknown object, matching the existing separation between ref parsing and object validation.

## Git compatibility

Current `gitrevisions` documentation defines `FETCH_HEAD` as the branch recorded by the last fetch. Current `git-fetch` documentation states that fetched ref names and object names are written to `FETCH_HEAD` and may be consumed by other Git commands. Native Git 2.47.3 probes additionally confirmed that revision resolution uses the first entry even when that entry is marked `not-for-merge`.

## Architecture

`pygit.fetch_head.read_fetch_head_oid()` owns parsing of the pseudo-ref metadata. `RefStore.resolve()` exposes `FETCH_HEAD` alongside `HEAD`, normal refs, tags, branches, and remote symbolic heads. No new special cases are required in ancestry, peel, tree-path, log, merge-base, or other revision consumers because they already share the unified resolver.

## SHA-256-native design

`FETCH_HEAD` is repository-local metadata and therefore continues to store pygit's 64-hex SHA-256 object IDs. The smart-HTTP SHA-1 interoperability boundary, native SHA map, pack conversion, object serialization, index format, and normal ref representation are unchanged.

## Regression coverage

`tests/test_phase186.py` covers:

- first-entry pseudo-ref selection
- independence from `not-for-merge`
- missing and empty metadata
- malformed object IDs
- direct `RefStore.resolve("FETCH_HEAD")`
- unified revision resolution
- parent traversal with `FETCH_HEAD^`
- tree-path resolution with `FETCH_HEAD:path`
- missing-object rejection at the revision layer
