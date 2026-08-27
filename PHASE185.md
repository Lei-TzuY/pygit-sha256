# Phase 185 — Fetch `--refmap`

Phase185 adds Git-style command-line destination remapping for explicit fetch refspecs.

## User-facing behavior

`pygit fetch` now accepts repeatable `--refmap=<refspec>` values:

```text
pygit fetch --refmap='+refs/heads/*:refs/remotes/origin/selected-*' origin main
```

The command-line `main` refspec still decides **what is fetched**. `--refmap` only replaces the configured `remote.origin.fetch` values used to decide which local remote-tracking ref receives a source-only command refspec.

Repeated refmaps are preserved and all matching mappings are updated. An explicit command destination continues to win:

```text
pygit fetch --refmap='+refs/heads/*:refs/remotes/origin/selected-*' origin main:local-main
```

updates `refs/heads/local-main` and does not additionally update the refmap destination.

An empty refmap explicitly disables configured destination mapping:

```text
pygit fetch --refmap='' origin main
```

The fetched object is still recorded in `FETCH_HEAD`, but no `remote.origin.fetch` destination is updated.

`--refmap` without any command-line refspec is rejected because the option is meaningful only for explicitly listed fetch refs.

## Git compatibility

Current upstream `git-fetch` documentation defines `--refmap=<refspec>` as replacing `remote.<name>.fetch` destination mapping when refs are listed on the command line. It may be repeated, and an empty refmap makes Git ignore configured refspecs and rely entirely on command-line refspecs.

Native Git 2.47.3 probes confirmed:

- `fetch --refmap=<mapping> origin main` applies the alternate mapping instead of the configured one;
- repeated refmaps can create multiple tracking destinations;
- `fetch --refmap='' origin main` fetches the source without updating the configured tracking destination;
- `--refmap` without a command-line refspec fails with `--refmap option is only meaningful with command-line refspec(s)`.

## Architecture

Phase185 extends the Phase184 explicit-fetch orchestrator only. Source selection continues to use command-line refspecs. Destination mapping now chooses either:

1. configured `remote.<name>.fetch` mappings when `--refmap` is absent; or
2. the exact ordered set of non-empty `--refmap` mappings when the option is present.

No SmartHttpClient, NativeImporter, object-store, index, or legacy Repository transport API is widened.

## SHA-256-native design

Refmap operates only on ref names. Imported objects, tracking refs, local branches, tags, and `FETCH_HEAD` continue to contain pygit's SHA-256 object IDs. The native Git smart-HTTP boundary remains SHA-1 and is unchanged.
