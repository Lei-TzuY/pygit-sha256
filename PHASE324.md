# Phase324: repository-level packfile-URI fetch transaction

Phase324 composes the exact-green Phase320–323 external-pack boundaries into one explicit repository operation without weakening pygit's SHA-256-native object model.

## Pipeline

`execute_packfile_uri_fetch_transaction()` performs four ordered steps:

1. **Bounded external-pack verification** — Phase320 downloads every `packfile-uris` descriptor with per-pack, aggregate-size, count, redirect, timeout, PACK framing, trailer SHA-1 and descriptor-checksum validation. No repository state changes here.
2. **SHA-256 staging/import** — Phase321 merges inline and verified external native objects, verifies every genuine remote SHA-1 object envelope, imports the complete graph in an isolated temporary SHA-256 store, then publishes only immutable content-addressed local objects.
3. **Root certification** — Phase322 proves each requested 40-hex remote-native SHA-1 root maps to a published 64-hex content-derived SHA-256 object with the expected Git object type.
4. **CAS ref publication** — Phase323 acquires canonical `<ref>.lock` files and performs the expected-old multi-ref transaction. This is deliberately the final mutable commit point.

The new orchestrator performs a small publication-plan preflight before network I/O so a ref cannot request a native root that the caller did not declare for certification.

## Failure model

A failure during descriptor verification, staged import, or root certification never reaches ref publication. A final CAS/lock/type failure is propagated and is not represented as a successful transaction result. Valid immutable SHA-256 objects imported before a final ref failure may remain unreachable; this is intentional and safer than exposing partially updated refs.

## SHA-256-native invariants

- wire/external-pack object identities remain genuine 40-hex SHA-1 values;
- local object identities remain full content-derived 64-hex SHA-256 values;
- there is no SHA-1 padding, truncation, translation, surrogate SHA-256 identity, or metadata-derived local object identity;
- refs are updated only after native content verification, complete graph import, local root reread, type certification, and expected-old CAS validation.

## Tests

`tests/test_phase324.py` verifies:

- exact boundary ordering: download → stage → certify → publish;
- propagation of configured network/resource limits;
- no ref publication call after download, staging, or certification failure;
- publication failures are not reported as success;
- inconsistent root/ref plans fail before network I/O;
- empty and pseudo-ref publication plans are rejected before the transaction begins.

The full repository test matrix remains authoritative for interaction with all earlier phases.
