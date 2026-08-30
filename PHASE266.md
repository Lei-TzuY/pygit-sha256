# Phase 266 - `rev-list --in-commit-order` object-type filters

Phase266 composes the SHA-256-native ordered object inventory with Git's
`--filter=object:type=commit|tree|blob` semantics.

## Supported compositions

- `--objects --in-commit-order --filter=object:type=commit|tree|blob`
- `--reverse`, `--skip`, `--max-count`, `--topo-order`, `--first-parent`, `--all`
- `--boundary`
- `--objects-edge`
- `--count`
- `-z` on the `--objects` path
- `--missing=allow-promisor|print|print-info`
- `--filter-provided-objects`
- `--filter-print-omitted`

## Git compatibility

Git documents `object:type=(tag|commit|tree|blob)` as omitting every object that
is not of the requested type. Explicitly provided objects bypass object filters
unless `--filter-provided-objects` is supplied. Pygit's current commit-rooted
ordered traversal therefore keeps explicitly provided positive commit roots by
default even for `object:type=tree` and `object:type=blob`, while older commits
reached from those roots remain ordinary traversed objects and are filtered.

Explicit `--objects-edge` records are a separate presentation channel and remain
visible regardless of the requested object type. Boundary commit frames are
ordinary filtered traversal records; for a non-commit type they disappear while
matching boundary snapshot objects retain their ordered positions.

Git 2.55's object-type filter does not populate the omitted-object set.
Consequently `--filter-print-omitted` is accepted but emits no `~<oid>` records
for `object:type`, matching the behavior already established by Phases255-257.

Annotated-tag filtering remains deferred because the ordered walker is currently
commit-rooted and does not yet model annotated tag traversal as a first-class
ordered inventory frame.

## SHA-256-native / partial-clone boundary

Filtering is applied to structured inventory entries before ordinary missing
validation and before rendering. Promised objects carry their known type in the
metadata-only inventory, so a promised blob can be discarded by
`object:type=tree` without materialization. If the promised object's type matches
the requested filter, the existing explicit missing channels remain
responsible for exposing its native transport identity.

No foreign SHA-1 is promoted into a repository-visible SHA-256 position, and no
single-object or batch fetch is required merely to classify the filter.

## Architecture

`rev_list_in_commit_order_object_type_cli` is a composition adapter over the
Phase259-264 structured walker. It deliberately reuses:

- ordered commit/snapshot inventory construction,
- edge/boundary overlap handling,
- line and NUL renderers,
- the mature Phase249-252 provided-root resolver.

This keeps one authoritative object walk and one set of SHA-domain rules rather
than filtering rendered text or duplicating traversal logic.

## Deferred work

- `object:type=tag`
- ordered `blob:limit=<n>[kmg]`
- ordered disk-usage composition
- additional Git filter families such as `tree:<depth>` and sparse filters
