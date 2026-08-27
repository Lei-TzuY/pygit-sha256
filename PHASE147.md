# Phase 147 — fsck reachable-object names

Phase 147 adds Git-style `fsck --name-objects` diagnostics. The integrity engine still validates and selects objects exactly as before; this phase layers deterministic rev-parse-style names over reachable object IDs so failures can be traced back to a ref, commit ancestry, or tree path.

## Commands

```bash
pygit fsck --name-objects
pygit fsck --name-objects --connectivity-only
pygit fsck --name-objects --tags
pygit fsck --name-objects HEAD~2
```

Git documents `--name-objects` as showing a name describing how a reachable object is reached, compatible with `rev-parse` syntax. Pygit follows that contract for roots whose spelling can be represented honestly.

## Naming rules

Names are seeded from:

- `HEAD`;
- refs such as `refs/heads/main` and `refs/tags/v1`;
- explicit positional fsck revisions, preserving the supplied expression;
- index roots as `:path`.

Reachability names then propagate through the object graph:

- first commit parent: `NAME~1`;
- additional merge parents: `NAME^2`, `NAME^3`, ...;
- commit tree: `NAME^{tree}`;
- tree entries: `NAME:path/to/object`;
- annotated-tag target: `NAME^{}`.

When an object has multiple possible names, the shortest/least-derived deterministic spelling wins. Reflog parser-position roots are intentionally not converted into fake `@{N}` expressions because their internal record index is not the user-facing reflog ordinal.

## Diagnostic behavior

`--name-objects` decorates reachable object diagnostics on stderr, for example:

```text
error: bad-tree-name <tree-oid> (HEAD:src): invalid entry name '.'
```

Phase 146 `--root` / `--tags` output is also decorated when the reported object has a reachable name. Unreachable and dangling objects remain undecorated: by definition there is no valid reachability path to name.

Without `--name-objects`, output is byte-for-byte on the existing Phase 146 formatting path. The option does not alter object validation, reachability, reference verification, lost-found behavior, exit status, or connectivity-only selection.

## Python API

```python
from pygit.fsck import fsck
from pygit.fsck_names import reachable_object_names

report = fsck(repo)
names = reachable_object_names(repo, report)
print(names.get(next(iter(report.reachable))))
```

The naming walk is read-only and bounded by `report.reachable`; unreadable objects are skipped rather than masking the original fsck error.
