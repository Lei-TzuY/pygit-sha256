# Phase329: named-remote packfile-URI fetch

Phase329 turns the exact-green Phase327/328 primitives into a repository-facing named-remote operation.

## What changed

`fetch_named_remote_with_packfile_uris()` now:

1. resolves a configured pygit remote URL without modifying repository state;
2. creates the protocol-v2 Smart HTTP packfile-URI client;
3. performs protocol-v2 `ls-refs` discovery first;
4. preserves the explicit `None` fallback when the initial server response is protocol v0;
5. derives remote-tracking publications with Phase328 from that exact advertisement;
6. reuses that advertisement in Phase327 rather than independently replanning refs;
7. runs bounded external-pack verification, content-derived SHA-256 staging, root certification, mutable-state guards/locks, and expected-old CAS publication;
8. returns the remote, URL, exact advertisement, publication plan, and complete repository transaction result.

A server that advertises v2 during discovery but later falls back during the terminating fetch is treated as a failed transaction rather than a successful fallback. No ref publication has occurred when that failure is raised.

## Remote-tracking policy

Phase329 inherits Phase328's intentionally narrow branch policy:

- `refs/heads/main` becomes `refs/remotes/origin/main`;
- nested branch names are preserved;
- existing local tracking tips become exact 64-hex SHA-256 CAS old values;
- missing tracking refs use pygit's established 64-hex zero creation sentinel;
- selected roots must certify as commits;
- tag publication remains outside this phase because annotated and lightweight tags need separate policy;
- the advertised default branch is returned as plan metadata, but `refs/remotes/<remote>/HEAD` is not mutated yet.

## SHA-256-native boundary

No identity translation shortcut is introduced.

- advertisement, wants, haves, and pack contents use genuine remote-native 40-hex SHA-1 object identities;
- external descriptor hashes remain native pack checksums;
- local objects and ref tips are full content-derived 64-hex SHA-256 identities;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-derived local object identity is used.

The named wrapper deliberately does **not** auto-derive haves from the legacy native-map or persist new native-map metadata. Supplying explicit haves remains possible, but the staging importer is authoritative and fails closed if a thin/incomplete graph cannot be imported. Persisting native-map state atomically with the publication transaction is a separate future boundary.

## Coordination

- latest `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact base: Phase328 / PR #304 head `048cd22e93389ff03dc0d0635e293f7e4be80dcf`
- Phase328 authoritative Tests #2803: success
- no `phase329` branch existed immediately before creation
- no PR newer than #304 existed at collision check

## Tests

`tests/test_phase329.py` covers:

- configured remote URL resolution;
- exact advertisement reuse across planner and repository adapter;
- all-branch and selected-branch publication planning;
- existing SHA-256 tracking-tip CAS and zero-SHA creation CAS;
- initial protocol-v0 fallback before publication planning;
- fail-closed protocol downgrade after successful v2 discovery;
- unknown/broken remote configuration before network construction;
- forwarding of haves, shallow/deepen options, timeouts, resource bounds, message, and external opener;
- rejection of an unadvertised branch before repository transaction entry.

The full inherited suite remains authoritative for native Git protocol-v2 behavior, packfile-URI checksum/download semantics, SHA-256 import, connectivity certification, metadata locking, and multi-ref CAS publication.
