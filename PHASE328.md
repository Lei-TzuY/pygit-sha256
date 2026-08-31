# Phase328 — packfile-URI remote-tracking publication planning

Phase328 adds the read-only planning layer between a remote Git advertisement and the exact-green Phase327 Smart HTTP repository transaction.

## Goal

Phase327 accepts explicit `expected_roots` and `PackfileUriRefPublication` mappings. Those are intentionally low-level trust-boundary inputs. Phase328 derives them from the repository's current remote-tracking state plus the exact advertisement returned by the remote.

For every selected `refs/heads/<branch>` record:

- the advertised 40-hex SHA-1 remains the remote-native identity;
- the required root type is `commit`;
- the destination is `refs/remotes/<remote>/<branch>`;
- an existing local tracking tip becomes the exact expected-old 64-hex SHA-256 CAS value;
- a missing tracking ref uses pygit's local 64-hex `ZERO_SHA` creation sentinel.

No hash-domain translation occurs during planning.

## Scope

The planner deliberately handles remote branches only. It does not yet publish tags, because annotated tags and lightweight tags have different root-type semantics and should not be collapsed into a single guessed rule. It also does not mutate `refs/remotes/<remote>/HEAD`; instead it returns `default_branch` when the advertisement's `HEAD` symref points to a selected advertised branch so a later phase can publish that symbolic metadata under its own lock/rollback rules.

Peeled `refs/tags/*^{}` records are never treated as branch roots.

## Failure model

Planning is read-only. Invalid remote names, malformed branch ref names, duplicate selections, unadvertised selections, malformed remote-native SHA-1 values, or malformed existing local SHA-256 refs fail before any network fetch or repository mutation performed by later phases.

Two remote branches may legitimately point at the same native commit. The resulting plan deduplicates that commit in `expected_roots` while retaining separate CAS publications for both tracking refs.

## SHA-256-native invariant

Phase328 preserves the repository's identity split:

- remote advertisement tips: genuine 40-hex SHA-1;
- local existing tracking refs: genuine 64-hex SHA-256;
- missing local tracking refs: the established local 64-hex zero CAS sentinel;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-derived local object identity.

Actual local SHA-256 identities for newly fetched objects are still created only by the Phase321 content importer used by the Phase327 transaction pipeline.

## Tests

`tests/test_phase328.py` covers:

- all advertised remote branches;
- existing local tracking refs as expected-old SHA-256 values;
- shared native tips across multiple branches;
- explicit branch selection and nested remote/branch names;
- tag and peeled-tag exclusion;
- missing and duplicate branch selection rejection;
- malformed native OID rejection;
- unsafe remote / branch selection input;
- read-only repository behavior.

The full inherited GitHub Actions Python 3.9 / 3.13 suite remains the authoritative compatibility gate.
