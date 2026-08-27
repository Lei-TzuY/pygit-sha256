# Phase 175 — Git-style push mirror mode

Phase 175 adds full-ref mirror push selection on top of Phase 174's annotated-tag native export.

## Scope

`pygit push --mirror <remote>` now mirrors every local ref beneath `refs/` to the selected remote. This is intentionally broader than `--all` plus `--tags`.

The mirror includes namespaces such as:

- `refs/heads/*`
- `refs/tags/*`
- `refs/remotes/*`
- `refs/notes/*`
- other well-formed refs stored beneath `.pygit/refs/` or in packed refs

Pseudo-refs outside `refs/`, including `HEAD`, are not mirrored.

## Update semantics

Mirror planning compares the full local `refs/*` set with the current receive-pack advertisement.

- a local ref missing remotely is created;
- a ref present on both sides is force-updated to the local value;
- an advertised remote `refs/*` ref missing locally is deleted;
- every generated mirror `PushSpec` is forced, matching Git's mirror behavior.

The planner enumerates loose and packed refs through the existing `plumbing.list_refs()` path.

Local remote-tracking refs are planned before local heads. The existing branch push transport refreshes `refs/remotes/<remote>/<branch>` after successful branch pushes; planning/sending pre-existing remote-tracking refs first prevents that local cache refresh from changing the source value of a later mirror update in the same sequential operation.

## Configuration

`remote.<name>.mirror=true` enables the same behavior when pushing to that remote without spelling `--mirror`.

Accepted boolean spellings follow the existing Git-compatible configuration convention:

- true: `true`, `yes`, `on`, `1`
- false: `false`, `no`, `off`, `0`

Invalid values fail rather than silently changing push selection.

Mirror selection takes precedence over `remote.<name>.push` and `push.default`.

## CLI compatibility

The following Git-incompatible combinations are rejected:

- `--mirror` plus explicit refspecs
- `--mirror` plus `--all` / `--branches`
- `--mirror` plus `--tags`
- `--mirror` plus `--delete`

The same restrictions apply when mirror mode comes from `remote.<name>.mirror=true`.

Options that Git permits alongside mirror mode continue to compose with the existing stack, including:

- `--atomic`
- `--prune` (redundant because mirror already deletes remote-only refs)
- `--follow-tags` (redundant because mirror already includes every local tag ref)
- `--force` (redundant because mirror specs are already forced)
- `--force-with-lease` (mirror's forced specs retain the established force precedence)
- `-o/--push-option`
- `-u/--set-upstream`

## Transport architecture

Phase 175 does not introduce a second transport implementation.

Mirror refs are represented using the existing `PushSpec` abstraction. Its `namespace` field already maps naturally to arbitrary fully-qualified refs: for example, `refs/notes/demo` becomes namespace `notes`, name `demo`, and `refs/remotes/upstream/main` becomes namespace `remotes`, name `upstream/main`.

The existing generic `push_ref()` and `delete_remote_ref()` paths already accept any fully-qualified `refs/*` target, while Phase 174's `NativeExporter` supports commit/tree/blob/tag objects. This lets mirror mode remain a selection/planning feature instead of duplicating smart-HTTP code.

Atomic mirror pushes reuse `push_atomic_specs()` unchanged.

## SHA-256-native boundary

All local refs continue to contain pygit's SHA-256 object IDs. Mirror planning compares ref names only. Existing transport/export code converts selected objects to native Git SHA-1 at the smart-HTTP boundary.

No local object, index, reflog, or ref storage format changes are introduced.

## Native Git checks

Native Git confirms that `--mirror`:

- mirrors all refs under `refs/`, explicitly including heads, remotes, and tags;
- force-updates changed remote refs;
- deletes remote refs missing locally;
- is also enabled by `remote.<name>.mirror`;
- rejects combination with explicit refspecs, `--all`, `--tags`, and `--delete`;
- can compose with `--atomic`, `--prune`, `--follow-tags`, force/lease options, and `-u`.

## Regression coverage

`tests/test_phase175.py` covers:

- configuration defaults and boolean aliases;
- invalid mirror configuration;
- heads/tags/remotes/notes selection;
- deletion of remote-only refs;
- ignoring advertised `HEAD`;
- forced mirror specs;
- remote-tracking-before-head ordering;
- explicit CLI mirror transport for generic refs, heads, and deletions;
- `remote.<name>.mirror=true` default behavior;
- native-incompatible selection combinations;
- configured mirror plus explicit-refspec rejection;
- atomic composition;
- CLI help exposure.
