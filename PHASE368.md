# Phase368 — EINTR-safe lock initialization durability

Phase368 extends the shared durability retry contract from lock release and
`FETCH_HEAD` publication to two compact lock-acquisition paths used by the
protocol-v2 packfile-URI transaction stack.

## Scope

The following initialization paths now use the shared `_fsync_retry(fd)` helper:

- repository-wide publication guards (`HEAD.lock`, `packed-refs.lock`,
  `promisor.json.lock`, `shallow.lock`)
- canonical target-ref `<ref>.lock` files used by packfile-URI CAS publication

Both compatibility publication-guard initialization and retained-descriptor
publication-guard initialization are covered.

`FETCH_HEAD.state.lock` is intentionally left for a follow-up because its
acquisition path also duplicates and closes descriptors as part of the Phase354
/ Phase356 ownership contract. Phase368 does not copy or partially rewrite that
larger state machine merely to gain the retry behavior.

## Semantics

Only `InterruptedError` from `os.fsync()` is retried, on the same descriptor.
Every other exception is propagated unchanged. A non-EINTR initialization
failure still removes the transaction-owned lock pathname and closes its
initialization descriptor according to the existing failure contract.

Successful retained locks still record descriptor-derived `(st_dev, st_ino)`
identity and keep a non-inheritable descriptor open through the publication
critical section. No lock names, marker bytes, ordering, CAS behavior, or
release semantics change.

## Compatibility and object-id domains

This phase changes only transient filesystem interruption handling. Remote/native
compatibility identities remain genuine full 40-hex SHA-1 values where Git
interoperability requires them. Local objects, refs, reflogs, `FETCH_HEAD`, and
object-map identities remain genuine content-derived full 64-hex SHA-256 values.
There is no padding, truncation, object-id text rehashing, surrogate SHA-256, or
metadata-derived local identity.

## Regression coverage

`tests/test_phase368.py` verifies:

- publication-guard fsync retries EINTR and preserves complete marker contents;
- retained publication guards still expose the correct inode identity and
  non-inheritable descriptor after an interrupted fsync;
- target-ref lock initialization retries EINTR while preserving marker and inode
  ownership;
- non-EINTR publication-guard and target-ref fsync failures still fail closed and
  remove the transaction-owned initialization lock.
