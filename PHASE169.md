# Phase 169 — `push --force-with-lease`

Phase 169 adds Git-style lease-protected forced push semantics on top of the
Phase 168 atomic receive-pack work.

## Supported forms

The modern push CLI now accepts:

```text
--force-with-lease
--force-with-lease=<refname>
--force-with-lease=<refname>:<expect>
--no-force-with-lease
```

The optional value is intentionally parsed only when attached with `=`.  This
matches Git's command-line shape and prevents a bare `--force-with-lease` from
mistaking the following repository name (for example `origin`) for a lease
argument.

`--no-force-with-lease` clears all preceding lease requests.  Later lease
requests can then establish a fresh policy.

## Lease expectations

A lease authorizes a normally non-fast-forward update only when the remote ref
still has the expected value.

- bare `--force-with-lease` protects every selected ref;
- `--force-with-lease=<refname>` protects only that ref;
- without an explicit `<expect>`, branch leases use the corresponding cached
  `refs/remotes/<remote>/<branch>` value;
- when no remote-tracking value exists, the implicit expectation is that the
  remote ref does not exist;
- `--force-with-lease=<refname>:` explicitly requires the remote ref to be
  absent;
- `--force-with-lease=<refname>:<expect>` resolves `<expect>` as a local pygit
  revision and converts its SHA-256 object graph identity to the native SHA-1
  value used by smart HTTP;
- a literal 40-hex explicit expectation is also accepted directly as a remote
  native SHA-1 interoperability value.

A stale expectation raises before receive-pack is sent and before cached local
remote-tracking state is changed.

## Force precedence

Native Git probes and the current git-push manual agree that ordinary force
requests disable lease safety checks.

Phase 169 therefore treats both forms as authoritative force:

```text
--force
+<refspec>
```

Global `--force` bypasses all leases.  A leading `+` bypasses a lease only for
that specific refspec.  This preserves the distinction between a lease-protected
conditional force and an explicitly unconditional force.

## Single-ref transport

`push_ref`, `push_branch`, and `delete_remote_ref` now accept an optional
`LeasePolicy` without changing existing callers.

When a lease is active, the transport:

1. discovers the actual remote ref value;
2. computes the applicable expected native value;
3. rejects stale information before any POST;
4. if the lease matches, permits that selected ref to bypass the ordinary
   fast-forward or existing-tag replacement restriction;
5. sends the existing single-ref receive-pack update and only then mutates local
   native/remote-tracking caches.

Deletion refspecs are protected by leases as well.

The modern CLI avoids the historical `Repository.push()` shortcut while a real
lease is active so the lease-aware discovery/preflight path is always used.
When no lease is active, all Phase 165–168 call signatures and monkeypatch
surfaces remain unchanged.

## Atomic composition

`push --atomic --force-with-lease ...` validates every applicable lease against
the same receive-pack advertisement used by the Phase 168 atomic batch.

If any selected ref is stale:

- no atomic receive-pack POST is sent;
- no native map is committed;
- no `refs/remotes/<remote>/*` cache is changed.

Only after all source, lease, fast-forward/tag, and delete preflight checks pass
does Phase 168 build the union pack and send the one atomic transaction.

## Compatibility probes

Native Git behavior was checked for the following boundaries:

- an implicit stale lease rejects with `stale info`;
- an explicit empty expectation rejects an existing remote ref;
- an implicit lease with no remote-tracking ref permits creation when the
  remote ref is absent;
- the same no-tracking lease rejects when the remote ref already exists;
- `--force --force-with-lease` performs an unconditional forced update;
- a leading `+` refspec likewise bypasses a stale lease for that ref.

The current `git push` documentation states that `--force-with-lease` overrides
the usual non-fast-forward restriction only when the current remote value is the
expected value, and that `--force` disables force-with-lease checks.

## Tests

`tests/test_phase169.py` covers:

- bare option parsing without consuming the repository positional;
- `--no-force-with-lease` reset behavior;
- global plus later ref-specific lease precedence;
- matching implicit leases authorizing a non-fast-forward rewrite;
- stale implicit lease rejection before receive-pack;
- no-tracking absent/existing remote behavior;
- explicit 40-hex native expectations;
- explicit local revision to native expectation conversion;
- ref-specific leases not forcing unrelated refs;
- global `--force` and leading `+` bypass behavior;
- CLI routing away from the legacy push shortcut while leased;
- lease-protected deletion;
- atomic multi-ref stale lease failure before the batch send.

## Deferred work

Phase 169 intentionally leaves these for later phases:

- `--force-if-includes` / `push.useForceIfIncludes`;
- explicit lease expectation revision-expression parity beyond the repository's
  existing revision resolver;
- remote groups;
- signed pushes and push-options;
- protocol-v2/send-pack negotiation improvements.
