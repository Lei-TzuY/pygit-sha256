# Phase 170 — `push --force-if-includes`

Phase 170 builds on Phase 169's `--force-with-lease` support with Git's
reflog-based protection against background fetches advancing an implicit lease
expectation.

## Why this exists

A bare `--force-with-lease` normally compares the remote branch with the local
remote-tracking ref.  That is safer than `--force`, but an editor, IDE, cron
job, or other background process can run `fetch` and advance the tracking ref
without the user having integrated those new remote commits.

In that case the implicit lease can still match even though the user never saw
or incorporated the newly fetched remote tip.  `--force-if-includes` adds a
second proof: the remote-tracking tip must have been integrated into the local
branch's reflog history before the rewrite is allowed.

## CLI

Phase 170 accepts:

```text
pygit push --force-with-lease --force-if-includes origin main
pygit push --force-with-lease=main --force-if-includes origin main
pygit push --force-with-lease --no-force-if-includes origin main
```

Repeated positive/negative includes flags use last-option-wins behavior.

The option is ancillary.  It does not itself force a push and is a no-op when:

- no `--force-with-lease` request applies to the ref, or
- the effective lease is `--force-with-lease=<ref>:<expect>` with an explicit
  expected commit.

This follows current Git documentation.

## Configuration

`push.useForceIfIncludes` is supported as a boolean configuration default:

```text
pygit config push.useForceIfIncludes true
```

Accepted boolean spellings are:

- true: `true`, `yes`, `on`, `1`
- false: `false`, `no`, `off`, `0`

An explicit `--force-if-includes` overrides a false config value, while
`--no-force-if-includes` overrides a true config value.

## Reflog proof

For an implicitly leased destination such as `refs/heads/main`:

1. Phase 169 first verifies that the advertised remote `main` still equals the
   expected value from `refs/remotes/<remote>/main`.
2. Phase 170 reads that local remote-tracking tip.
3. The local destination-name branch (`main`) must exist.
4. At least one `refs/heads/main` reflog entry must point to a commit from which
   the remote-tracking tip is reachable.
5. If no such reflog state exists, the push is rejected with a
   `remote ref updated since checkout` error before receive-pack is sent.

Checking the destination-name branch is important.  Native Git behavior was
probed with `topic:main`: if `main`'s reflog proves that `origin/main` had been
integrated, the push may proceed even when `topic` itself never contained that
remote tip.  Conversely, a differently named source branch cannot substitute
for a missing local `main` branch.

When the implicit lease expects remote-ref absence because no remote-tracking
value exists, there is no remote tip for the includes guard to prove, so the
additional check is a no-op.

## Native Git probes

The following user-visible behavior was verified against native Git:

### Background fetch not integrated

Starting with local `main=A`, a second clone advances remote `main` to `B`, and
the first clone runs `fetch`, so `origin/main=B` while the local `main` reflog
has never contained a commit including `B`.

```text
git push --force-with-lease origin main:main
```

can force the rewrite because the implicit lease matches `B`.

Adding:

```text
--force-if-includes
```

rejects the push with `remote ref updated since checkout`.

### Integrated before rewrite

If local `main` first moves to `B`, so its reflog records a state including
`B`, and is then rewritten away from `B`, the same
`--force-with-lease --force-if-includes` push succeeds.

### Explicit expectation

```text
git push --force-with-lease=main:<B> --force-if-includes ...
```

still succeeds when `<B>` matches the remote even if the local reflog did not
integrate `B`, because Git defines force-if-includes as a no-op for exact
`:<expect>` leases.

### Delete and destination-name behavior

The includes guard also applies to an implicitly leased deletion.  Native Git
rejects deletion when a background-fetched remote tip has not been integrated
into the corresponding local branch reflog.

A `topic:main` probe also confirmed that the relevant reflog is local `main`,
not local `topic`.

## Interaction with Phase 169

`LeasePolicy` now carries an optional `force_if_includes` bit.  The existing
`require_lease()` function performs the additional reflog proof only after the
implicit lease value itself matches.

This design intentionally leaves all Phase 169 transport function signatures
unchanged:

- `push_ref()`
- `push_branch()`
- `delete_remote_ref()`
- `push_atomic_specs()`

The Phase 168 atomic path therefore receives the protection automatically.
Every selected ref is preflighted before the single atomic receive-pack POST;
one failed includes proof aborts the entire batch with no local native-map or
remote-tracking mutation.

## Force overrides

Phase 169 already models native Git's force precedence:

- global `--force` disables lease checks,
- a leading `+` on a refspec bypasses the lease for that ref.

Because force-if-includes is attached to the lease proof, those force paths also
bypass the includes check, matching Git.

## SHA-256-native boundary

No object identity model changes in Phase 170.  Reflog and ancestry checks use
local SHA-256 commit IDs.  Phase 169 still converts implicit/explicit lease
expectations at the existing native SHA-1 smart-HTTP boundary.

## Tests

`tests/test_phase170.py` covers:

- option extraction and last-option-wins behavior,
- `push.useForceIfIncludes` parsing and CLI precedence,
- invalid config values,
- rejection after an unintegrated background-style tracking advance,
- acceptance when an older local reflog state included the remote tip,
- destination-name reflog behavior for `topic:main`,
- rejection when the destination-name local branch is missing,
- exact `:<expect>` no-op semantics,
- expected-absence leases,
- deletion protection,
- atomic preflight rejection before batch POST,
- config-driven CLI activation and explicit disable,
- global force bypass.

## Scope boundary

Phase 170 intentionally does not add unrelated push features such as remote
groups, push-options, signed pushes, mirror/prune behavior, or protocol-v2
send-pack negotiation.
