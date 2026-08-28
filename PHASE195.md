# Phase195: fetch prefetch namespace

Phase195 adds Git-style `fetch --prefetch` on top of Phase194 / PR #170.

## Behavior

`pygit fetch --prefetch <remote>` rewrites configured fetch destinations into the `refs/prefetch/` namespace while keeping the configured source selection unchanged. A normal mapping such as:

```text
+refs/heads/*:refs/remotes/origin/*
```

therefore updates:

```text
refs/prefetch/remotes/origin/*
```

instead of moving the user's ordinary remote-tracking refs.

The implementation keeps explicit command destinations explicit. For example, under `--prefetch`, a source-only command refspec can still use the rewritten configured mapping, while `dev:peek` remains an explicit local destination. An explicit `--refmap` replaces configured mappings and therefore leaves nothing for `--prefetch` to rewrite.

`--prefetch` also composes with named-remote multi-fetch (`--multiple`, `--all`, remote groups), FETCH_HEAD controls, atomic rollback, pruning, force, tag policy, quiet/verbose output, and dry-run through the existing fetch stack.

Prefetch pruning is constrained to the rewritten configured destination domain. Both loose and packed refs under `refs/prefetch/` are considered. Ordinary `refs/remotes/*` and `refs/tags/*` are not treated as part of the configured prefetch prune domain.

Direct URL fetches have no configured refspec, so `--prefetch` does not invent a synthetic destination for them.

## Git compatibility

Current Git `git-fetch` documentation defines `--prefetch` as modifying the configured refspec so that all configured destinations are placed under `refs/prefetch/`. `git-maintenance` uses this behavior to download objects ahead of a foreground fetch without moving the user's normal remote-tracking branches. The maintenance task separately disables tags, so Phase195 does not implicitly reinterpret `--prefetch` as `--no-tags`.

## SHA-256-native design

Prefetch refs store pygit's ordinary 64-hex SHA-256 object IDs. No object format, pack conversion, index format, native SHA map, or smart-HTTP SHA-1 interoperability boundary changes in this phase.

## Verification targets

Focused Phase195 tests cover configured refspec rewriting, SHA-256 prefetch ref persistence, ordinary remote-tracking isolation, pruning, explicit-destination behavior, and single/multi-remote CLI forwarding. The full Python 3.9 / 3.13 GitHub Actions matrix remains the regression gate.
