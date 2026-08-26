# Phase 124 — multi-stage index conflict entries

Phase 124 upgrades pygit's readable JSON index from a stage-0-only model to a backward-compatible Git-style multi-stage index. Stages 1, 2, and 3 can now be persisted, inspected, and resolved as real object records instead of being rejected by the Phase 118 revision parser.

## Index model

The existing `Index.entries` mapping remains stage 0 only, preserving the API expected by porcelain and older tests. Conflict entries are stored separately as `(path, stage)` records for:

- stage 1 — merge base;
- stage 2 — ours;
- stage 3 — theirs.

`IndexEntry` now carries a `stage` value from 0 through 3. Stage-0 JSON records keep the historical schema exactly; only stages 1-3 add a `"stage"` field. Existing repositories therefore load without migration.

The stage-aware query surface includes `get(path, stage)`, `stage_entries()`, `all_entries(include_unmerged=True)`, `paths(include_unmerged=True)`, and `has_unmerged()`.

## `update-index --index-info`

Both accepted forms now work with stages 0-3:

```text
MODE OID<TAB>PATH
MODE OID STAGE<TAB>PATH
```

For example:

```bash
printf '100644 <base> 1\tfile.txt\n100644 <ours> 2\tfile.txt\n100644 <theirs> 3\tfile.txt\n' |
  pygit update-index --index-info
```

A mode-0 record removes every stage for the named path, matching Git's index-info removal behavior. Mutations remain transactional at the Python helper level: malformed later input does not publish earlier records.

Normal worktree staging resolves an unmerged path just like `git add`: stages 1-3 are removed and replaced by one stage-0 entry containing the worktree content. `--cacheinfo` remains a direct stage-0 insertion and can coexist with explicitly supplied conflict stages, matching native Git's low-level behavior.

## Inspection and object expressions

`pygit ls-files --stage` now emits the actual stage number for every stored index record. Plain cached output emits one pathname per stored record, so an unmerged three-stage path appears three times just as it does in Git.

The shared revision resolver now supports:

```text
:1:file.txt    # merge-base blob
:2:file.txt    # ours blob
:3:file.txt    # theirs blob
```

These expressions automatically work in plumbing already backed by `resolve_revision()`, including:

```bash
pygit rev-parse ':2:file.txt'
pygit cat-file -p ':3:file.txt'
```

Phase 118's disambiguation remains unchanged: only `:0:` through `:3:` are stage prefixes. An index path literally named `4:name` is still addressed as `:4:name`.

## Refresh semantics

`update-index --refresh` treats an unmerged path as needing update rather than indexing into a nonexistent stage-0 record. This keeps conflict state explicit and avoids accidental crashes or silent resolution.

## Scope boundary

Phase 124 supplies the underlying persistent conflict-stage model and low-level plumbing. Existing high-level merge/cherry-pick porcelain may continue to use their established conflict-state files until a later phase explicitly migrates those workflows to populate stages 1-3 automatically.

## Regression coverage

`tests/test_phase124.py` covers stage persistence/reload, legacy stage-0 JSON compatibility, stage-aware revision resolution, `ls-files --stage`, cached duplicate-path behavior, mode-0 removal, worktree conflict resolution, `--cacheinfo` coexistence, refresh behavior, atomic rejection of invalid stages, installed CLI round trips, `cat-file` integration, and Phase 118 colon-path disambiguation.
