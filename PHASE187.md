# Phase 187 - Direct URL fetch sources

Phase187 extends the Phase183-186 fetch porcelain so a one-shot smart-HTTP URL
can be used directly as the repository argument without first configuring a
named remote.

## User-visible behavior

```text
pygit fetch https://example.test/repo.git
pygit fetch https://example.test/repo.git maint
pygit fetch https://example.test/repo.git maint:peek
pygit fetch --refmap='+refs/heads/*:refs/remotes/peek/*' \
    https://example.test/repo.git maint
pygit log FETCH_HEAD
```

A direct URL is intentionally not materialized as a remote. The command does
not add `remote.*` configuration, does not create an `origin` tracking ref, and
does not persist a synthetic remote native-SHA map.

With no command-line refspec the advertised `HEAD` is fetched and recorded as
the mergeable FETCH_HEAD entry. With an explicit source-only refspec, the
selected ref is fetched into FETCH_HEAD only. A command `src:dst` updates that
local destination, and an explicit `--refmap` can opt into one or more mapped
destinations even though no named remote exists.

`--tags` explicitly imports all advertised tags into `refs/tags/*` while the
ordinary automatic-follow behavior remains available unless `--no-tags` is
used.

Phase187 accepts the HTTP and HTTPS transports already implemented by
`SmartHttpClient`. Other Git URL transports remain outside the current smart
HTTP transport layer.

## Git compatibility

Current upstream `git-fetch` documentation states that `<repository>` may be a
named remote or a URL. Its examples explicitly demonstrate peeking at a remote
without configuration:

```text
git fetch git://git.kernel.org/pub/scm/git/git.git maint
git log FETCH_HEAD
```

The same documentation distinguishes command-line refspecs from named-remote
`remote.<name>.fetch` mappings. A raw URL has no named-remote fetch mapping, so
a source-only command refspec does not implicitly create a remote-tracking ref.

Native Git 2.47.3 local repository probes additionally confirmed:

- `git fetch <repository-url>` with no refspec fetches `HEAD` into FETCH_HEAD
  and creates no local branch/tracking ref;
- `git fetch <repository-url> dev` fetches only `dev` into FETCH_HEAD and still
  creates no local ref without an explicit destination.

## Architecture

`pygit.fetch_direct` owns URL-source orchestration. It reuses the established
`SmartHttpClient`, Phase183 tag-follow helper, Phase184 destination update
rules, and Phase184/186 FETCH_HEAD serialization/revision behavior.

Direct URL fetches deliberately use a transient native SHA mapping. This may
redownload an already-known native graph on a later one-shot URL fetch, but it
avoids leaving synthetic remote identity behind; SHA-256 object storage still
deduplicates the imported objects naturally.

Direct-URL pruning is rejected in this phase because the existing prune engine
is explicitly scoped to a named remote tracking namespace. A later phase can
add destination-refspec-scoped URL pruning without pretending that a raw URL
owns a named remote.

## SHA-256-native design

The URL only selects the transport endpoint. Imported objects, explicit local
destinations, and FETCH_HEAD all remain SHA-256-native. Native SHA-1 remains
confined to smart-HTTP negotiation and pack conversion; object serialization,
index data, local refs, and revision identity are unchanged.
