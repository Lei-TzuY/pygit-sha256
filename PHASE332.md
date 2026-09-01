# Phase 332 — library unborn clone API parity

Phase331 made the public `pygit clone` command understand protocol-v2's explicit
`unborn HEAD` response.  The lower-level Python APIs used by applications still
entered their normal branch-selection logic directly, however, so an empty
remote had no concrete `refs/heads/*` object id and failed before the Phase317
metadata-only initializer could run.

Phase332 closes that API mismatch for the two protocol-v2 clone entry points that
already own modern shallow/partial transport:

- `clone_partial_repository()`
- `clone_shallow_repository()`

## Behavior

Each function keeps its existing argument validation first.  With the original
production `SmartHttpV2FetchClient` installed it then calls Phase331's
`try_clone_explicit_unborn_remote()` using the same mode metadata:

- partial clone forwards the normalized filter, single-branch choice, branch
  request, and ordered server options;
- shallow clone forwards depth, single-branch choice, branch request, and
  ordered server options.

Only an explicit Phase315 `unborn HEAD` record can short-circuit the function.
The returned repository is exactly the Phase317/331 metadata-only empty clone.
Protocol-v0 and ordinary non-empty protocol-v2 advertisements return `None` from
the preflight and continue through the historical fetch/import implementation.

An explicit `branch_name` remains a request for a concrete remote branch.  It is
therefore rejected for an unborn target exactly as in Phase331/native Git rather
than falling back into the old branch selector.

## Injection compatibility

Older tests and applications replace the module-local `SmartHttpV2FetchClient`
to provide a deterministic transport double.  A hidden real-network preflight
would bypass that dependency injection.  Each module therefore remembers its
original client class and enables the new preflight only while that exact class
is still installed.

This preserves the established client-constructor and fake-transport seams while
adding the production behavior.

## SHA-256-native invariants

No new object identity path exists in this phase.

- explicit unborn state remains reference metadata, not an object id;
- no 40-hex native SHA-1 is padded, truncated, translated, or treated as local;
- no fake/zero 64-hex local branch tip is created;
- the empty path performs no fetch, pack import, object-store write, shallow
  boundary write, or promisor-object persistence;
- partial-clone configuration may exist exactly as Phase331 specifies, but no
  object is promised until a future concrete filtered fetch.

## Coordination

- actual `main` remained `bfcbae64e4dc9997b915c16e1aa923a951090083` at phase start;
- exact base: Phase331 / PR #308 head
  `40dacfe1dd2f05d6fb67864d291523f3add21036`;
- Phase331 authoritative Tests #2826: Python 3.9 / 3.13 both 2374 passed,
  runner Git 2.55.0;
- Phase321–330 are an independent packfile-URI/object-map stack and are untouched;
- Phase332 was checked free immediately before branch creation.

## Tests

`tests/test_phase332.py` covers:

- direct partial/shallow API short-circuiting;
- exact filter/depth/server-option/single-branch forwarding;
- validation before network preflight;
- explicit-branch failure propagation;
- preservation of replaced-client dependency injection seams;
- returning before ordinary fetch-client construction after a successful unborn
  preflight.

The execution container cannot reliably clone GitHub, so the exact-head GitHub
Actions Python 3.9 / 3.13 matrix is the authoritative full-suite gate.
