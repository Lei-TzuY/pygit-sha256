# Repository integrity plumbing (`fsck`)

Phase 60 adds a repository checker for pygit's native SHA-256 object database.
It validates storage before trusting graph traversal, then checks connectivity
from repository roots. Phase 142 adds Git-style reflog reachability to the
installed command while preserving the historical Python API default. Phase 143
adds recovery materialization for dangling tips through `--lost-found`. Phases
144-145 add explicit reachability heads and independent reference-database
verification. Phase 146 adds root-commit and annotated-tag diagnostics.

## CLI

```bash
pygit fsck
pygit fsck --full
pygit fsck --connectivity-only
pygit fsck --unreachable
pygit fsck --no-dangling
pygit fsck --root
pygit fsck --tags
pygit fsck --lost-found
pygit fsck --no-reflogs
pygit fsck --no-references
pygit fsck --strict
pygit fsck HEAD
```

A normal full scan exits `0` when no integrity errors are found. Structural or
connectivity errors exit `1`. Warnings are non-fatal unless `--strict` is used.

The default output reports dangling unreachable roots, for example:

```text
dangling commit 0123...abcd
```

`--unreachable` prints every unreachable object instead. `--no-dangling`
suppresses reachability diagnostics while retaining integrity errors.

`--root` reports every validated commit with no traversal-visible parent,
including unreachable root commits. `--tags` reports every validated annotated
tag and its target using Git-compatible `tagged <type> <target> (<name>) in
<tag-oid>` records. Both are database-wide full-scan diagnostics and remain
independent of explicit fsck heads and `--no-dangling`. As in native Git, they
are suppressed by `--connectivity-only`, which does not inventory the complete
object database. A shallow-boundary commit is presented as a synthetic root.

The installed `pygit fsck` command treats reflog entries as reachability roots by
default, matching Git's recovery model. `--no-reflogs` disables that policy and
restores the ref/index/shallow-only view. This applies to both full and
`--connectivity-only` scans.

Supplying positional objects, such as `pygit fsck HEAD~2`, replaces the default
refs/index/reflog reachability roots with exactly those resolved objects.
`--cache` adds index entries back as heads. Reference-database consistency is
still checked independently unless `--no-references` is supplied.

## Lost-found recovery

`pygit fsck --lost-found` materializes the already-computed dangling tips under
`.pygit/lost-found`, following Git's recovery layout:

- dangling commits -> `.pygit/lost-found/commit/<oid>` containing `<oid>\n`;
- dangling trees and tags -> `.pygit/lost-found/other/<oid>` containing `<oid>\n`;
- dangling blobs -> `.pygit/lost-found/other/<oid>` containing the exact raw blob bytes.

Only dangling tips are written; the complete unreachable closure below a dangling
commit is not duplicated. Reflog-protected objects therefore remain absent from
`lost-found` by default, while `--no-reflogs --lost-found` can deliberately
surface historical tips that are retained only by reflogs.

Recovery is fail-closed. All selected dangling objects are readable before any
recovery file is created, fsck integrity errors suppress materialization, and
symlinked/non-directory `lost-found` paths are rejected. Individual files are
written through temporary files and atomically replaced, making repeated runs
idempotent and preventing stale recovery content from winning.

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

Without explicit positional objects, the graph walk treats these as roots:

- `HEAD`
- all loose and packed refs below `refs/`
- every index entry
- commits named by `.pygit/shallow`

The installed CLI additionally reads every regular reflog below `.pygit/logs`
and adds each non-zero `old_oid` and `new_oid` as a recovery root. The same
strict safe-path and record parser used by `reflog show` / `reflog expire` is
reused, so malformed existing reflogs fail closed instead of silently weakening
retention. `--no-reflogs` skips reflog discovery and parsing completely.

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

This mode starts from the selected refs/index/shallow roots and, by default for
the installed command, reflog recovery roots. It discovers only reachable
objects. It still validates the object links and types it visits, but deliberately
does not inventory unrelated loose/packed objects or report dangling objects that
were never reached.

It is useful for a fast "can the published/staged/recoverable graph be
traversed?" check. Add `--no-reflogs` when only the currently published/staged
graph should participate. Because unrelated dangling objects are not inventoried,
`--connectivity-only --lost-found` does not synthesize recovery files for them.
`--root` and `--tags` are likewise suppressed in this mode rather than showing a
partial database-wide diagnostic.

## Python API

```python
from pygit import fsck
from pygit.fsck_diagnostics import annotated_tags, root_commits
from pygit.fsck_lost_found import write_lost_found

report = fsck(repo)
assert report.ok
print(report.checked_objects)
print(report.reachable)
print(report.dangling)
print(root_commits(repo, report))
print(annotated_tags(repo, report))

recovery_report = fsck(repo, include_reflogs=True)
write_lost_found(repo, sorted(report.dangling))

for issue in report.issues:
    print(issue.render())
```

The exported fsck API consists of:

- `fsck(repo, connectivity_only=False, include_reflogs=False, heads=(), include_index=None)`
- `FsckReport`
- `FsckIssue`

The recovery helper is `write_lost_found(repo, dangling_oids)` and returns
`LostFoundRecord` entries describing the files it wrote. Phase 146's reporting
helpers are `root_commits(repo, report)` and `annotated_tags(repo, report)`.

For backward compatibility, direct Python callers keep the Phase 60 root set
unless `include_reflogs=True` is requested. The installed CLI intentionally uses
`include_reflogs=True` by default and maps `--no-reflogs` to `False`.

`FsckReport.errors` and `.warnings` provide severity-filtered views; `.ok` is
true when no error-level issue exists.
