# Repository integrity plumbing (`fsck`)

Phase 60 adds a repository checker for pygit's native SHA-256 object database.
It validates storage before trusting graph traversal, then checks connectivity
from repository roots.

## CLI

```bash
pygit fsck
pygit fsck --full
pygit fsck --connectivity-only
pygit fsck --unreachable
pygit fsck --no-dangling
pygit fsck --strict
```

A normal full scan exits `0` when no integrity errors are found. Structural or
connectivity errors exit `1`. Warnings are non-fatal unless `--strict` is used.

The default output reports dangling unreachable roots, for example:

```text
dangling commit 0123...abcd
```

`--unreachable` prints every unreachable object instead. `--no-dangling`
suppresses reachability diagnostics while retaining integrity errors.

## Storage verification

Full mode inventories storage directly rather than beginning with
`ObjectStore.all_shas()`, so a broken pack index cannot prevent diagnosis of the
rest of the repository.

Checks include:

- loose object pathname shape (`objects/aa/<62 hex>`)
- loose-object decompression and SHA-256 verification through `ObjectStore`
- `.idx` magic/version, fanout monotonicity, exact size, sorted unique OIDs,
  offsets, and SHA-256 checksum
- matching `.pack`/`.idx` pairs
- pack magic/version/object count and SHA-256 checksum
- every indexed packed object can be decoded and hashes back to its indexed OID
- reconstructed object serialization retains the stored object ID

## Connectivity roots

The graph walk treats these as roots:

- `HEAD`
- all loose and packed refs below `refs/`
- every index entry
- commits named by `.pygit/shallow`

The index is a root because staged objects must not be reported as dangling merely
because they have not been committed yet.

A shallow boundary is special: its commit and tree remain checked, but parent
edges beyond that boundary are intentionally not required to exist. This keeps
valid shallow clones from being diagnosed as corrupt.

## Object graph checks

### Commits

- `tree` must be a 64-hex OID naming a tree object
- parents must name commit objects
- parent links are skipped beyond declared shallow boundaries

### Trees

- supported modes: `040000`, `100644`, `100755`, `120000`, `160000`
- entry names must be single safe path components
- duplicate names are rejected
- mode-to-object type relationships are checked:
  - `040000` -> tree
  - `100644`, `100755`, `120000` -> blob
  - `160000` -> commit

### Annotated tags

- target OID is validated
- declared target type must be blob/tree/commit/tag
- actual target type must match the declaration

### Whole graph

- missing referenced objects are errors
- wrong target types are errors
- object cycles are reported
- reachable/unreachable sets are computed after validation
- a dangling object is an unreachable object not referenced by another
  unreachable object

## Connectivity-only mode

```bash
pygit fsck --connectivity-only
```

This mode starts from refs/index/shallow roots and discovers only reachable
objects. It still validates the object links and types it visits, but deliberately
does not inventory unrelated loose/packed objects or report dangling objects that
were never reached.

It is useful for a fast "can the published/staged graph be traversed?" check.

## Python API

```python
from pygit import fsck

report = fsck(repo)
assert report.ok
print(report.checked_objects)
print(report.reachable)
print(report.dangling)

for issue in report.issues:
    print(issue.render())
```

The exported API consists of:

- `fsck(repo, connectivity_only=False)`
- `FsckReport`
- `FsckIssue`

`FsckReport.errors` and `.warnings` provide severity-filtered views; `.ok` is
true when no error-level issue exists.
