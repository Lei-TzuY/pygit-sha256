# Phase 164 — Branch tracking setup

Phase 163 taught modern status rendering how to read Git-style
`branch.<name>.remote` and `branch.<name>.merge` upstream configuration. Phase
164 closes the other half of that loop: clone, branch creation, and checkout
now create and maintain that tracking metadata instead of relying on status's
legacy implicit `origin/<current>` compatibility fallback.

## Git compatibility target

Current Git documents `branch.<name>.remote` and `branch.<name>.merge` as the
configuration pair defining a branch's upstream. `git branch --track` and
`git checkout --track` populate those entries, and ordinary branch creation
from a remote-tracking branch is controlled by `branch.autoSetupMerge`.

The implementation follows the public behavior rather than inventing a
pygit-specific tracking database.

## Shared tracking layer

`pygit/tracking.py` owns branch/upstream interpretation for the new CLI paths.
It can resolve:

- remote-tracking sources such as `origin/feature`
- `remotes/origin/feature`
- `refs/remotes/origin/feature`
- local branches, represented by Git's special `branch.<name>.remote=.` form

The layer writes:

```ini
[branch]
topic.remote = origin
topic.merge = refs/heads/feature
```

The repository's existing config representation stores dotted branch names as
keys inside the `[branch]` section, so no config format migration is required.

## `pygit branch`

The modern branch CLI now supports:

```text
pygit branch topic origin/feature
pygit branch -t topic origin/feature
pygit branch --track topic origin/feature
pygit branch --track=direct topic origin/feature
pygit branch --track=inherit topic base
pygit branch --no-track topic origin/feature
pygit branch --set-upstream-to=backup/release topic
pygit branch --unset-upstream topic
```

`-t` and a bare `--track` mean direct tracking. The long spelling also accepts
`--track=direct` and `--track=inherit` without consuming the branch name as an
optional argparse value.

Branch rename moves `branch.<old>.remote/merge` to the new branch name. Branch
deletion clears the old tracking keys.

## `branch.autoSetupMerge`

When neither `--track` nor `--no-track` is specified, Phase 164 honors:

- `true` (default): track a remote-tracking start point
- `false`: never create tracking automatically
- `always`: track local or remote branch start points directly
- `inherit`: copy a local start branch's existing upstream
- `simple`: direct-track a remote start only when local and remote branch names
  match

This keeps explicit CLI intent above configuration defaults.

## `pygit checkout`

Checkout now supports tracking-aware branch creation:

```text
pygit checkout -b topic origin/feature
pygit checkout --no-track -b topic origin/feature
pygit checkout --track origin/feature
pygit checkout -t origin/feature
```

`--track` without `-b` derives the local branch name from the remote-tracking
branch, matching Git's convenience form.

A plain missing local branch name also performs Git's default remote guess:

```text
pygit checkout feature
```

If exactly one remote contains `feature`, pygit creates local `feature`, points
it at that remote-tracking ref, records the upstream, and checks it out. If
multiple remotes contain the same branch, the operation is rejected unless
`checkout.defaultRemote` selects one of them.

## Clone tracking

The modern clone wrapper keeps the existing `Repository.clone()` transport and
object-import implementation, then records the checked-out initial branch as
tracking `origin/<branch>`:

```text
branch.main.remote = origin
branch.main.merge = refs/heads/main
```

This matches native clone behavior while avoiding any duplicate smart-HTTP
implementation.

## Architecture

Phase 164 does not modify the large legacy `pygit/cli.py` or the repository
storage API. Instead, `pygit/application.py` routes `clone`, `branch`, and
`checkout` through focused modern command modules:

- `pygit/clone_cli.py`
- `pygit/branch_cli.py`
- `pygit/checkout_cli.py`
- `pygit/tracking.py`

They continue to call the existing `Repository.clone`, `Repository.branch`,
and `Repository.checkout` methods for the actual repository mutations.

## Regression coverage

`tests/test_phase164.py` covers:

- `branch -t` direct remote tracking
- bare long `--track` grammar
- default remote auto-setup
- `branch.autoSetupMerge=false`
- explicit `--no-track`
- direct tracking of a local branch using `.`
- `--track=inherit`
- `--set-upstream-to` / `--unset-upstream`
- moving tracking config during branch rename
- checkout `-b` auto tracking
- checkout `-b --no-track`
- `checkout --track` without `-b`
- plain unique-remote checkout guessing
- ambiguous remote guessing and `checkout.defaultRemote`
- clone tracking config construction
- branch/checkout help visibility

The full Python 3.9 and 3.13 matrix remains the release gate because routing
branch and checkout through modern command modules must not regress older CLI
coverage.
