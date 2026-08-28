# Phase 188 — Multi-remote fetch orchestration

Phase188 extends the Phase181–187 fetch stack with Git-style remote groups,
`fetch --multiple`, `fetch --all`, `fetch.all`, and
`remote.<name>.skipFetchAll`.

## User-facing behavior

```text
pygit fetch --all
pygit fetch --multiple origin backup
pygit fetch --multiple mirror-group origin
pygit fetch mirror-group
```

A remote group is configured through the existing flattened Git-style config:

```ini
[remotes]
mirror-group = origin backup
```

Group member order and duplicates are preserved. Under `--multiple`, each
positional argument is a remote or group; command-line fetch refspecs are not
accepted in that mode.

`fetch --all` fetches every configured remote except one with a true
`remote.<name>.skipFetchAll`. Argument-less `fetch` does the same when
`fetch.all=true`; `--no-all` suppresses that configured behavior. Explicitly
naming a repository overrides `fetch.all`.

## FETCH_HEAD aggregation

A logical multi-source fetch replaces FETCH_HEAD once for its first attempted
source and appends later source results. Passing `--append` causes even the
first source to append to the pre-existing file.

Execution is sequential in this phase. Upstream Git can parallelize
multi-remote fetches with `--jobs`; pygit does not pretend to have that
scheduler yet. Sequential execution preserves the same repository-visible
ref and FETCH_HEAD semantics.

A failure from one source does not prevent later sources from being attempted.
The overall command returns non-zero if any source failed.

## Git compatibility checked

Current `git-fetch` documentation defines four relevant invocation forms:

- `git fetch [<repository> [<refspec>...]]`
- `git fetch <group>`
- `git fetch --multiple [(<repository>|<group>)...]`
- `git fetch --all`

It states that `--multiple` permits several repository/group arguments and no
refspecs, and that `--all` excludes remotes configured with
`remote.<name>.skipFetchAll`. `fetch.all=true` makes an argument-less fetch try
all remotes, while `--no-all` or an explicit repository overrides it.

Native Git 2.47.3 local probes additionally confirmed that:

- a group expands in configured order;
- duplicate expansion is observable under `--multiple`;
- a remote marked `skipFetchAll=true` is skipped by `--all`;
- explicitly fetching that remote is still allowed;
- an explicit repository overrides `fetch.all=true`.

## Architecture

New `pygit.fetch_multiple` owns group expansion, all-remote selection, boolean
config parsing, and sequential aggregate failure handling. Existing
`fetch_configured` and `fetch_porcelain` continue to own each remote's actual
selection, tag/prune policy, import, native-map update, and FETCH_HEAD
serialization.

No transport API is widened. Multi-fetch is orchestration over already-tested
single-remote fetch operations.

## SHA-256-native design

This phase changes only which configured remote is fetched and how multiple
results are orchestrated. Objects, local refs, remote-tracking refs and
FETCH_HEAD remain SHA-256-native. Native SHA-1 stays confined to the existing
smart-HTTP negotiation and pack-conversion boundary.
