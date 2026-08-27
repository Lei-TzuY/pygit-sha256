# Phase 165 — upstream-aware pull and push defaults

Phase 165 connects the branch tracking metadata added in Phases 163–164 to
actual remote operations. The object database, index, refs, and network import/
export layers remain SHA-256-native and unchanged.

## Pull

`pygit pull` now resolves the current branch's configured upstream before
fetching/integrating:

- `branch.<name>.remote` selects the default repository.
- `branch.<name>.merge=refs/heads/<branch>` selects the branch to integrate.
- `branch.<name>.remote=.` integrates a local branch without performing a fetch.
- partial tracking configuration is rejected instead of silently mixing a
  configured half with the legacy default.
- with no tracking configuration, the compatibility fallback remains
  `origin/<current-branch>`.
- `pygit pull <remote>` keeps the configured merge branch when that remote is
  the configured upstream remote; otherwise it falls back to the current local
  branch name.
- `pygit pull <remote> <branch>` explicitly selects both values.

This follows Git's documented default pull behavior: a no-argument pull uses
`branch.<name>.remote` and `branch.<name>.merge`, and a dot remote represents
the current local repository.

## Push

No-argument `pygit push` now chooses its repository with Git-style precedence:

1. explicit command-line repository
2. `branch.<name>.pushRemote`
3. `remote.pushDefault`
4. the branch's non-dot upstream remote
5. `origin`
6. the only configured remote, when unambiguous

If several remotes remain and no default is configured, pygit asks for an
explicit repository instead of choosing arbitrarily.

`pygit push -u/--set-upstream` writes the successful current-branch push back
as:

- `branch.<name>.remote=<remote>`
- `branch.<name>.merge=refs/heads/<name>`

The existing `Repository.push()` transport API is deliberately unchanged in
this phase, so pushes still update the same-named remote branch. Destination
refspec/push.default expansion can be layered independently later.

## Compatibility notes

Git documents that upstream branches are the default for `pull` and usually
for `push`, while `branch.<name>.pushRemote` overrides `remote.pushDefault`,
which itself overrides the normal upstream remote for pushing. Git also allows
`branch.<name>.remote=.` with `branch.<name>.merge` to pull from another local
branch.

Phase 165 implements those remote-selection semantics without pretending to
support Git's entire push refspec or pull rebase/ff policy surface.

## Tests

`tests/test_phase165.py` covers:

- configured remote/merge parsing
- rejection of partial upstream configuration
- legacy no-config pull fallback
- explicit pull remote precedence
- pushRemote / pushDefault / upstream precedence
- an actual local `remote=.` pull fast-forward
- `push -u` using the resolved push default and persisting tracking config

The full existing test suite remains the regression gate because `pull` and
`push` are now routed through modern focused CLI handlers in `application.py`.
