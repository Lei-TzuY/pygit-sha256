# Phase 387 — Verify Git bundle v2/v3 payloads

Phase387 establishes the file-format trust boundary between protocol-v2
`bundle-uri` discovery/download and any future repository import.

It deliberately starts from the exact-green Phase379 bundle-uri discovery line
and consumes only raw bundle bytes.  It does not depend on the still-independent
Phase381 downloader and performs no repository mutation.

## Public API

`pygit.git_bundle` adds:

- `BundlePrerequisite`
- `GitBundlePayload`
- `parse_git_bundle(data)`

The result separates three different notions that must not be conflated:

1. objects physically contained in the pack;
2. prerequisite objects that the receiver must already have;
3. a v3 `filter` capability indicating an intentionally incomplete reachable
   object graph.

## Bundle header grammar

The parser accepts the documented signatures:

```text
# v2 git bundle
# v3 git bundle
```

For v2, the header is:

```text
*prerequisite
*reference
<blank line>
PACK...
```

For v3, capabilities precede prerequisites/references:

```text
@object-format=sha1
@filter=<filter-spec>       # optional
*prerequisite
*reference
<blank line>
PACK...
```

Capabilities appearing after prerequisites/references, prerequisites appearing
after references, duplicate capabilities/prerequisites/refs, malformed 40-hex
native object IDs, invalid ref names, missing blank-line framing, NUL/CR header
bytes, and bundles with no references fail closed.

Git bundle capabilities are not negotiated.  Unknown v3 capabilities therefore
raise rather than being ignored.  The supported capability set is currently:

- `object-format`
- `filter`

Absent `object-format` defaults to SHA-1, matching native Git.  Explicit
`object-format=sha256` is rejected because pygit's current remote/native
compatibility domain remains SHA-1 even though its local object store is
SHA-256-native.

## Pack verification

The embedded SHA-1 pack is verified before it is returned:

- `PACK` signature;
- pack version 2/3;
- declared entry count;
- every object variable-length header;
- OFS_DELTA base-offset framing;
- REF_DELTA 20-byte native base identity framing;
- every zlib stream reaches EOF;
- decompressed byte count matches the entry header;
- no bytes remain between the final entry and trailer;
- SHA-1 pack trailer equals `sha1(pack_without_trailer)`.

This matters because a malicious payload can recompute a valid checksum over an
otherwise malformed pack.  Phase387 checks structure in addition to the digest.

For a bundle without prerequisites, the existing `PackParser` additionally
expands the pack into `NativeObject` values and every advertised ref tip must be
present in that object set.

## Thin/prerequisite bundles

Git bundles created with revision exclusions may use thin packs.  Their delta
bases can intentionally live outside the bundle in the prerequisite graph.
Phase387 therefore does **not** pretend that raw bytes alone can fully expand
such a graph.

For prerequisite bundles it still validates the complete bundle header plus pack
entry framing/checksum, preserves the prerequisite native OIDs/comments, and
returns the verified raw pack.  `objects` remains `None` until a later
prerequisite-aware importer can provide and verify the external bases.

Prerequisites are not treated as a shallow boundary and are not converted into
promisor state.

## Filter capability

A v3 `filter=<filter-spec>` capability is surfaced as `filter_spec` and makes
`is_self_contained` false even when there are no prerequisites.  The contained
pack may still be parsed into objects, but callers cannot mistake that set for a
complete reachable object graph.  A later import phase must preserve the
promisor semantics required by the bundle format.

## SHA-256-native boundary

All identities carried by bundle headers and packs remain genuine remote-native
40-hex SHA-1 values.

Phase387 performs no:

- SHA-1 padding/truncation/translation;
- surrogate SHA-256 generation;
- identifier-text rehashing;
- object-store writes;
- ref/HEAD/reflog updates;
- shallow/promisor mutation;
- bundle URI network access.

Any later local identity must still be derived from actual canonical object
content at the existing SHA-256 importer/store boundary.

## Native Git compatibility

Focused tests create real repositories and use native Git to produce:

- full bundle v2;
- full bundle v3;
- incremental v2 bundle with a prerequisite;
- v3 bundle from a SHA-256 repository.

The full SHA-1 bundles are accepted by both `git bundle verify` and
`parse_git_bundle()`.  The incremental bundle preserves its prerequisite and is
not falsely expanded as a self-contained graph.  The SHA-256-native Git bundle
is intentionally rejected at pygit's remote hash-domain boundary.

The tests also exercise a valid-checksum-but-invalid-structure pack, proving the
new boundary is stricter than checksum-only verification.

## Coordination

- actual `main` at phase start:
  `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact base: Phase379 / PR #356 head
  `584942445e96c2a1d01275d4e9f0691b32e26748`
- Phase379 authoritative Tests #3178: 2399 passed on Python 3.9 and 3.13,
  runner Git 2.55.0
- Phase381 download work exists independently but had no verified PR at the
  Phase387 coordination check and is untouched
- Phase386 template-directory work is independent and untouched
- Phase387 was collision-checked immediately before branch creation

This phase intentionally remains read-only and is intended to become the input
validator for a later bundle download/import transaction.
