# Phase 177 — Remote push URLs

Phase 177 adds Git-style `remote.<name>.pushurl` and multi-URL push fan-out on top of Phase 176 remote groups.

## Goals

A named remote is not necessarily a single network destination. Git permits:

- multiple `remote.<name>.pushurl` values;
- multiple `remote.<name>.url` values when no pushurl is configured;
- one URL for fetch compatibility while pushing to several mirrors.

The push planner and transport stack already has significant behavior attached to one named remote: default refspecs, mirror mode, prune, follow-tags, leases, atomic transactions, push-options, hooks, native-object conversion, and remote groups. Phase 177 therefore keeps the remote identity stable and executes the existing push pass once per selected URL.

## Selection rules

For a named remote `origin`, push destinations are resolved in this order:

1. every non-empty `remote.origin.pushurl`, in configuration order;
2. otherwise every non-empty `remote.origin.url`, in configuration order;
3. otherwise pygit's historical single URL stored in `.pygit/config.json`.

A configured pushurl list replaces the ordinary URL list for push operations.

An empty multi-valued entry clears earlier values through the existing `GitConfig.get_all()` reset convention. Since pygit currently models one repository-local INI configuration scope, a completely cleared pushurl list falls back to the URL list.

## Fan-out semantics

Each push destination receives a complete ordinary push pass.

This is important for destination-dependent behavior:

- `--mirror` must inspect each destination's advertised refs separately;
- `--prune` must decide deletions from each destination's advertisement;
- `--follow-tags` skips tag names that already exist on that particular destination;
- `--atomic` is one atomic receive-pack transaction per URL, not one cross-server transaction;
- force-with-lease checks compare against the destination currently being contacted;
- push-options and the pre-push hook are applied to each destination pass.

The first destination failure stops later URLs for that named remote. Successful earlier destinations are not rolled back. This matches native Git's multi-pushurl behavior.

Phase 176 remote groups remain a separate orchestration boundary: a failed member remote still allows the group driver to continue to later member remotes. Within the failed member itself, however, later push URLs are not attempted.

## Compatibility architecture

The existing push stack historically discovers its endpoint through `Repository._read_config()` and the JSON remote's `url` field. Adding a URL parameter to every planner and transport would create a wide compatibility change.

Phase 177 instead introduces `pygit.push_urls.use_push_url()`:

- it temporarily overlays one selected URL in the in-memory config view;
- it never writes `.pygit/config.json`;
- it restores the repository instance exactly after the scope exits;
- existing `Repository.push()`, `push_ref()`, `push_atomic_specs()`, mirror, prune, and follow-tags code need no signature changes.

This keeps the established SHA-256-native object/ref architecture and smart-HTTP SHA-1 conversion boundary unchanged.

## Git compatibility probes

Native Git 2.47.3 was checked with local bare repositories.

Observed behavior:

- two `remote.origin.pushurl` entries both receive the pushed branch;
- with no pushurl, two `remote.origin.url` entries both receive pushes;
- pushurl replaces the ordinary URL list for pushing;
- if the first push URL fails, later push URLs are not attempted;
- if an earlier URL succeeds and a later URL fails, the earlier update remains and the overall command fails.

Current Git documentation also states that fetch uses only the first ordinary URL, while push affects all pushurls or, when there are none, all ordinary URLs. Fetch-side multi-URL selection remains separate from this push-focused phase.

## Regression coverage

`tests/test_phase177.py` covers:

- legacy JSON URL fallback;
- pushurl-over-url precedence;
- multiple ordinary URLs as push destinations;
- empty pushurl reset;
- in-memory-only scoped override and full restoration;
- ordered CLI fan-out;
- first-failure short-circuit;
- atomic flag replay for every URL;
- Phase176 group continuation after one member's pushurl failure.

## Scope boundary

Phase 177 intentionally does not add fetch-side multi-URL configuration, `git remote set-url` porcelain, signed pushes, or protocol-v2 send-pack negotiation. Those remain independent follow-up phases.
