# Phase 163 — configured status upstream tracking

Phase 163 replaces the modern status layer's assumption that the interesting
tracking branch is always `origin/<current>` whenever Git-style branch tracking
configuration is present.

## Configuration

Pygit's existing config command stores a key such as:

```text
pygit config branch.main.remote backup
pygit config branch.main.merge refs/heads/release
```

as section `branch` with keys `main.remote` and `main.merge`. Phase 163 consumes
that existing representation directly.

The two keys are interpreted together:

- `branch.<name>.remote` chooses the fetch/upstream remote.
- `branch.<name>.merge` chooses the branch to integrate, normally
  `refs/heads/<branch>`.
- remote `.` means the upstream is another local branch.

This follows Git's documented upstream model: `branch.<name>.remote` and
`branch.<name>.merge` together define the branch's upstream tracking branch.

## Status behavior

Configured tracking takes priority over the previous implicit
`origin/<current>` status heuristic.

For example, if `main` tracks `backup/release`, short status may show:

```text
## main...backup/release [ahead 1]
```

Porcelain v2 emits:

```text
# branch.oid <sha256>
# branch.head main
# branch.upstream backup/release
# branch.ab +1 -0
```

The repository remains SHA-256-native; the branch header itself does not change
object-id width semantics.

## Local upstreams

Git allows `branch.<name>.remote=.` to mean the local repository. Phase 163
supports that shape as well:

```text
branch.main.remote = .
branch.main.merge = refs/heads/integration
```

Status then compares `main` with local branch `integration` and displays the
upstream as `integration` rather than `./integration`.

## Gone upstreams

A configured upstream remains meaningful even if its tracking ref disappears.
Phase 163 preserves that state instead of silently dropping the relationship.

Short status:

```text
## main...backup/release [gone]
```

Long status:

```text
Your branch is based on 'backup/release', but the upstream is gone.
```

Porcelain v2 still emits:

```text
# branch.upstream backup/release
```

but deliberately omits `# branch.ab`, because there is no upstream commit to
compare. This matches native Git's porcelain-v2 behavior for a gone upstream.
`--no-ahead-behind` does not turn a gone upstream into the unrelated `+? -?`
case.

## Partial/unsupported configuration

Once either tracking key exists, configured tracking is authoritative. A
partial configuration (for example a remote with no merge branch) therefore
suppresses the old implicit origin/current fallback instead of silently
selecting a different upstream.

Phase 163 currently supports `refs/heads/<name>` merge targets plus a simple
branch-name convenience spelling. Other ref namespaces/refspec transformations
are left for a future refspec-mapping phase.

## Compatibility boundary

`Repository.status()` itself still contains its historical implicit
`origin/<current>` calculation. Phase 163 intentionally leaves that public API
unchanged and overrides the upstream only in the modern presentation layer.

When neither `branch.<name>.remote` nor `branch.<name>.merge` exists, modern
status also keeps the Phase150 legacy fallback. This prevents older callers and
regression fixtures that only materialize `refs/remotes/origin/<current>` from
breaking while newly configured repositories use the Git-style upstream path.
A later migration can remove that fallback after repository/clone tracking
configuration is made authoritative everywhere.

## Verification

`tests/test_phase163.py` covers:

- configured remote and merge branch overriding legacy `origin/<current>`;
- porcelain-v2 configured upstream metadata;
- local `.` upstream tracking;
- gone upstream rendering in short, long, and porcelain-v2 formats;
- no `branch.ab` header for gone upstreams, even with
  `--no-ahead-behind`;
- partial configuration suppressing fallback;
- backward-compatible no-config fallback;
- full and simple merge-branch parsing;
- resolver metadata and detached HEAD handling.
