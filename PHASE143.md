# Phase 143 — `fsck --lost-found` recovery

Phase 143 turns Phase 60/142 dangling-object analysis into an explicit recovery
workflow without changing refs or object reachability.

## Command

```bash
pygit fsck --lost-found
pygit fsck --no-reflogs --lost-found
```

The installed command still performs a normal full integrity scan. If the scan
has no error-level issues, each dangling tip is materialized below
`.pygit/lost-found`.

## Git-compatible layout

Current Git documents `--lost-found` as writing dangling commits below
`lost-found/commit` and other object types below `lost-found/other`, with blob
files containing the blob payload rather than the object name. pygit follows
that protocol using SHA-256 object IDs:

- commit: `lost-found/commit/<oid>` -> `<oid>\n`
- tree/tag: `lost-found/other/<oid>` -> `<oid>\n`
- blob: `lost-found/other/<oid>` -> exact binary blob payload

Only `FsckReport.dangling` is materialized. Objects that are merely unreachable
below a dangling commit remain recoverable through that commit and are not
redundantly copied into `lost-found`.

## Reflog interaction

Phase 142 makes reflogs recovery roots for the installed CLI. Therefore a commit
still named by a reflog is not dangling and is not written by default.
`--no-reflogs --lost-found` deliberately disables that protection and can expose
historical tips that are otherwise retained solely through reflogs.

## Safety

Recovery is intentionally fail-closed:

- all selected dangling objects are read before filesystem mutation;
- any fsck error suppresses lost-found writes;
- an existing symlink or non-directory at the lost-found root/category is rejected;
- recovery files are written via same-directory temporary files and atomically replaced;
- repeated recovery runs are idempotent and refresh stale file contents.

No refs, reflogs, index entries, object files, packs, or worktree files are
modified.

## Python API

```python
from pygit.fsck_lost_found import write_lost_found

records = write_lost_found(repo, sorted(report.dangling))
```

Each returned `LostFoundRecord` contains the OID, recovery category, and output
path.

## Scope boundary

This phase does not add explicit fsck head arguments, `--root`, `--tags`,
`--name-objects`, progress reporting, or alternate-object-pool full scans. Those
remain separate compatibility work.
