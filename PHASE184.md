# Phase 184 — explicit fetch refspecs and `FETCH_HEAD`

Phase184 extends the configured fetch stack from Phases181–183 with Git-style
command-line refspec selection and fetch-result metadata.

## User-facing behavior

`pygit fetch` now accepts zero or more explicit refspecs after the remote:

```text
pygit fetch origin dev
pygit fetch origin dev:tmp
pygit fetch origin +seen:seen maint:tmp
pygit fetch origin 'refs/heads/*:refs/remotes/origin/*' '^refs/heads/private-*'
```

When command-line refspecs are present they decide which advertised refs are
fetched. A command refspec with no destination still consults the configured
`remote.<name>.fetch` refmap to decide whether the fetched source should update
a remote-tracking ref. An explicit destination overrides that configured map.
Short destinations such as `tmp` are normalized to `refs/heads/tmp`.

Positive wildcard and negative refspecs compose with the Phase183 tag policy.
Pruning follows the active destination domain rather than treating configured
remote mappings as a selection domain when the command line supplied its own
source-only refspec.

Every porcelain fetch writes `.pygit/FETCH_HEAD`. The file stores pygit's local
64-hex SHA-256 object IDs, because those are the repository-native object names.
Ordinary configured fetch marks the advertised remote default branch as the
merge candidate and marks other fetched refs `not-for-merge`. Explicit command
refspec sources are merge candidates. `fetch -a/--append` appends instead of
overwriting the file.

## Git compatibility

Current upstream `git-fetch` documentation specifies that command-line
`<refspec>` values replace configured refspecs for deciding what is fetched,
while `remote.<name>.fetch` can still act as the destination refmap for a
command-line source with no destination. It also specifies that fetched object
names/ref names are written to `.git/FETCH_HEAD`, and that `--append` preserves
existing entries.

Native Git 2.47.3 local probes confirmed:

- `git fetch origin dev` writes `dev` as a mergeable FETCH_HEAD entry and still
  updates `refs/remotes/origin/dev` through the configured wildcard refmap;
- `git fetch origin dev:refs/remotes/origin/x` updates only `origin/x`;
- ordinary `git fetch origin` marks the remote default branch mergeable and
  other configured branches `not-for-merge`;
- `git fetch --append origin main` preserves an earlier FETCH_HEAD entry;
- `git fetch --prune origin dev` does not prune unrelated stale configured
  tracking refs merely because the configured refmap exists.

## Architecture and SHA-256 design

`pygit.fetch_porcelain` layers explicit command selection over the mature
Phase183 transport helpers. `SmartHttpClient` and `NativeImporter` signatures
remain unchanged. Destination updates support remote-tracking refs, tags, and
local heads; local-head updates require fast-forward unless the refspec has
`+`.

`pygit.fetch_head` owns the metadata writer. Unlike native Git repositories
using SHA-1, pygit writes SHA-256 IDs to FETCH_HEAD because local object storage,
refs, and revision identity are SHA-256-native. Transport-side SHA-1 remains
confined to the existing smart-HTTP conversion boundary.
