# Phase 166 — push.default and destination refspec semantics

Phase 165 taught `pull` and `push` how to choose the correct repository. Phase
166 adds the next Git compatibility layer: deciding **which local branch updates
which remote branch** when `pygit push` has no explicit refspec.

## Implemented behavior

### Ref selection precedence

`pygit push` now resolves what to push in Git's documented order:

1. command-line refspec(s),
2. `remote.<name>.push`,
3. `push.default`.

The educational config store is scalar, so Phase 166 accepts one configured
`remote.<name>.push` value and also permits shell-style whitespace-separated
branch refspecs inside that scalar.

### `push.default`

Supported values:

- `simple` — default; push the current branch to the same name, but when pushing
  back to its pull/upstream remote require the configured upstream branch to
  have the same name.
- `current` — push the current branch to the same-named remote branch.
- `upstream` — push the current branch to its configured upstream branch and
  require the selected push remote to be that upstream remote.
- `tracking` — deprecated Git synonym for `upstream`.
- `matching` — push local branches that also exist under the selected cached
  remote-tracking namespace.
- `nothing` — reject a push that omitted refspecs.

`simple` also follows Git's central/non-central distinction: when the selected
push remote differs from the branch's pull remote, it behaves like `current`.
Without explicit tracking, `origin` (or the sole remote) is treated as the
central pull-side fallback and therefore requires an upstream unless
`push.autoSetupRemote=true`.

### `push.autoSetupRemote`

For default `simple`, `current`, and `upstream` pushes without an existing
upstream, `push.autoSetupRemote=true` causes the successful push destination to
be persisted as:

```text
branch.<name>.remote = <remote>
branch.<name>.merge  = refs/heads/<destination>
```

This is the same tracking pair introduced by Phase 164.

### Branch refspecs

Phase 166 supports the branch-focused subset needed by these defaults:

```text
main
HEAD
main:release
HEAD:release
+main:release
:
+:
```

The leading `+` forces the selected update. `:` / `+:` select matching branches.
Fully-qualified `refs/heads/...` names are accepted.

Deliberately deferred: deletion refspecs, tag refspecs, arbitrary revision
expressions, wildcard refspecs, negative refspecs, `--all`, `--mirror`, and
`--tags`.

## Target-aware transport

The historical `Repository.push(remote, force=...)` API is intentionally kept
unchanged. Same-name pushes of the current branch still use it directly.

A new `pygit.push_transport.push_branch()` helper reuses the same smart-HTTP
receive-pack, SHA-256/native object mapping, fast-forward guard, pre-push hook,
and remote-tracking update logic when a resolved refspec needs:

- a non-current source branch,
- a destination branch with another name,
- or one branch of a `matching` push.

This keeps existing callers source-compatible while allowing `main -> release`
style pushes.

For Phase 166, multi-ref `matching` pushes are executed as deterministic
per-branch updates rather than one atomic receive-pack transaction. The branch
selection semantics are the focus of this phase; atomic multi-ref transport can
be added later.

## Compatibility notes

Current Git documentation defines `push.default=simple` as the default and
lists `nothing`, `current`, `upstream`/`tracking`, `simple`, and `matching`.
Git also gives command-line refspecs precedence over `remote.<name>.push`, which
in turn precedes `push.default`.

Native probes used while implementing this phase confirmed:

- `simple` rejects a same-remote upstream whose branch name differs,
- `simple` behaves like `current` when pushing to a different repository,
- `simple` without an upstream rejects the central/default remote,
- `current` pushes the same branch name without requiring tracking,
- `upstream` targets the configured merge branch and rejects another remote,
- `nothing` rejects a no-refspec push,
- `remote.origin.push=main:dst` overrides `push.default=nothing`,
- `push.autoSetupRemote=true` allows the default push and records upstream.

## Regression coverage

`tests/test_phase166.py` covers:

- default `simple` and `tracking` alias parsing,
- `simple` central safety and non-central behavior,
- `current`, `upstream`, `nothing`, and invalid values,
- explicit/forced refspec parsing,
- `remote.<name>.push` precedence,
- matching branch intersection and `:` syntax,
- `push.autoSetupRemote`,
- CLI target-aware upstream pushes,
- `-u` with an explicit destination,
- actual target ref selection in the target-aware transport helper.

The complete existing Python 3.9 / 3.13 suite remains the final regression gate.
