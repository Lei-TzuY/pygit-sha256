# Phase 358 — Programmatic unborn partial/shallow clone APIs

Phase331 made the public `pygit clone` command understand protocol-v2's explicit
`unborn HEAD` response.  The direct Python partial/shallow clone helpers still
projected discovery through the historical `Advertisement` shape, lost the
unborn sentinel, and then failed because an empty remote has no concrete branch
OID.  Phase358 closes that API/CLI split.

## Behavior

`clone_partial_repository()` and `clone_shallow_repository()` now perform one
unborn-aware protocol-v2 discovery in production.

- explicit `unborn HEAD symref-target:refs/heads/<branch>` enters the exact
  Phase317 metadata-only initialization path;
- no concrete object fetch/import occurs for an empty remote;
- partial clones retain `protocol.version=2`, promisor/filter configuration, but
  create no object promise until a concrete remote object exists;
- shallow clones retain protocol-v2 mode but create no `.pygit/shallow` file;
- explicit `branch_name` still means a concrete remote branch request and fails
  against an unborn target, with the destination rolled back like Phase331;
- v0 continues to fail with the established partial/shallow "requires protocol
  version 2" contract;
- an empty-looking advertisement without the explicit Phase315 unborn sidecar is
  not inferred to be an empty repository.

## Single discovery reuse

The production unborn query client now retains the exact validated
`ProtocolV2Capabilities` used for its `ls-refs` request.  `CloneRefDiscovery`
keeps those capabilities beside Phase315's `ProtocolV2LsRefsResult`.

For a non-empty v2 remote, partial/shallow clone selection reuses:

1. the same validated capability advertisement;
2. the same `ls-refs` `Advertisement`;
3. the established strict fetch request builder/parser and `PackParser`.

There is no second capability GET or second `ls-refs` request before the initial
terminating fetch.  Older test doubles that expose only the historical API keep
working: if no retained capabilities are available, the adapter delegates to
the old fetch-side discovery contract.

## Compatibility seams

Historical Phase204/214 tests and callers replace either the fetch client class
or its `discover_refs()` / `fetch()` methods.  Those overrides deliberately stay
on the old call shape and do not receive a hidden unborn preflight.

The CLI similarly stops preflighting production partial/shallow paths because
the public Python helpers own that discovery now.  Ordinary legacy
`Repository.clone()` still uses Phase331's CLI preflight because that API has no
unborn-aware discovery channel.

## Native Git baseline

A real SHA-256 empty bare repository with initial branch `topic/empty` establishes
these Git behaviors:

- `git clone --filter=blob:none file://...` leaves HEAD symbolic/unborn, has no
  refs or shallow file, and persists the promisor/filter configuration;
- `git clone --depth=1 file://...` also leaves HEAD symbolic/unborn with no
  shallow boundary and no persistent single-branch fetch refspec;
- neither path invents an object id merely because clone metadata exists.

Phase358 repeats this differential baseline on the CI runner Git.

## SHA-256-native invariants

Unborn remains reference state, not an object identity.

- no zero-OID branch tip;
- no fabricated local 64-hex SHA-256;
- no SHA-1 padding, truncation, identifier re-hashing, or surrogate SHA-256;
- the empty path performs capability/`ls-refs` discovery only and never fetches
  or imports object content;
- local object identity remains content-derived at the established importer;
- no `.pygit/promisor.json` object promise is written for an empty partial clone.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase352 / PR #329 head
  `1680f8e0e55ccd57f2567db8e0f4737123f49431`;
- Phase352 authoritative Tests #2981: Python 3.9 / 3.13 both 2406 passed,
  Git 2.55.0;
- Phase353–357 are an independent packfile-URI publication-guard line;
- Phase358 was collision-checked before branch creation and is kept independent
  of those changes.

## Tests

`tests/test_phase358.py` covers direct partial/shallow empty clones, exact config
and no-object state, explicit-branch cleanup, v0 behavior, non-empty selected-ref
discovery reuse, no repeated capability lookup in both fetch adapters, CLI
preflight ownership, and native Git empty partial/shallow behavior.

The execution container cannot reliably clone `github.com`; exact-head GitHub
Actions Python 3.9 / 3.13 is therefore the authoritative full-suite gate.
