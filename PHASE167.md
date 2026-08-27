# Phase 167 — expanded push refspecs

Phase 167 extends the Phase 166 push destination planner beyond branch-only refspecs while keeping pygit's object database SHA-256-native and retaining the historical `Repository.push(remote, force=False)` API.

## Added behavior

- empty-source deletion refspecs such as `:old` and `:refs/tags/v1`
- `push -d/--delete <remote> <ref>...`
- `push --all` / `--branches` for all local branches
- `push --tags` for all local tags, including composition with explicit refspecs
- fully qualified tag refspecs such as `refs/tags/v1` and `refs/tags/v1:release`
- one-star pattern refspecs such as `refs/heads/feature/*:refs/heads/archive/*`
- corresponding tag patterns under `refs/tags/*`
- detached-HEAD tag-only pushes when the remote is explicit
- generic smart-HTTP ref updates for branches and tags
- receive-pack zero-object-ID deletions
- local remote-tracking branch cleanup after a successful remote branch deletion

## Git compatibility

Current `git push` documentation defines the refspec form as `[+]<src>[:<dst>]`. In particular:

- an empty `<src>` deletes `<dst>`
- `--all` pushes every ref under `refs/heads/` and cannot be combined with explicit refspecs
- `--tags` pushes every ref under `refs/tags` in addition to explicit command-line refspecs
- a pattern refspec has one `*` in the source and one `*` in the destination; the captured text is substituted into the destination
- branch updates remain fast-forward-only unless forced
- existing tag refs are not updated without force

Phase 167 implements those public semantics for branch and tag namespaces. Negative refspecs, `--prune`, `--mirror`, `--follow-tags`, arbitrary revision-expression sources, and atomic multi-ref transactions remain later work.

## SHA-256-native design

Local commits, tags, refs, and the object store continue to use 64-hex SHA-256 object IDs. The smart-HTTP push path converts source objects to the remote Git-native representation through the existing `NativeExporter`; no SHA-1 IDs are persisted as pygit object identities. Deletions send the protocol's zero native object ID and do not mutate local object storage.

## Compatibility preservation

- `Repository.push(remote, force=False)` is unchanged.
- `push_branch()` remains available with its Phase 166 signature and delegates to the generic ref transport.
- current-branch same-name pushes keep using the legacy `Repository.push()` path.
- Phase 166 `PushSpec.source`, `.target`, and `.force` remain source-compatible; Phase 167 adds namespace/deletion metadata.

## Tests

`tests/test_phase167.py` covers deletion planning and transport, tag refspec parsing, inferred tag destinations, branch/tag wildcard expansion, deterministic `--all`/`--tags` planning, detached tag-only pushes, and conservative existing-tag replacement behavior. The full repository test matrix is required on Python 3.9 and Python 3.13 before the phase is considered complete.
