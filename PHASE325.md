# Phase 325 — Guard mutable repository state before packfile-URI ref publication

Phase324 composes the verified external-pack pipeline into a single repository-level operation. Phase325 hardens the final handoff to ref publication by proving that the mutable publication surface did not change while download, SHA-256 staging/import, and root certification were running.

## Why this boundary exists

The packfile-URI path deliberately allows verified immutable SHA-256 objects to be published before refs. That is safe because a failed fetch may leave unreachable content-addressed objects without making repository history visible.

Mutable repository state is different. Before Phase323 commits refs, Phase325 snapshots the exact bytes/existence of the small state surface that must remain unchanged during pre-publication work:

- `HEAD`
- `logs/HEAD`
- `packed-refs`
- `.pygit/promisor.json`
- `.pygit/shallow`
- every target ref named by the publication plan
- every corresponding target reflog

Immediately before Phase323 acquires canonical ref locks and performs expected-old CAS publication, Phase325 rereads those paths and compares the snapshots byte-for-byte. Any creation, deletion, or content change aborts the transaction before ref publication.

This is intentionally fail-closed. A concurrent writer changing a target ref would already be caught by Phase323 CAS, but detecting the broader mutable-state race before publication also protects HEAD/reflog/promisor/shallow invariants and catches accidental side effects in earlier packfile-URI stages.

## What is intentionally excluded

`.pygit/objects` is not part of the mutable snapshot. Phase321 is explicitly allowed to publish verified immutable SHA-256 objects before refs. If a later boundary fails, those objects may remain unreachable and can be collected later; they do not constitute a partially published fetch.

The guard also does not roll back a concurrent writer. If external repository state changes while the fetch is in flight, the transaction aborts and leaves that independently written state intact rather than overwriting it.

## SHA-256-native invariants

Phase325 changes no wire or object identity behavior:

- remote object roots remain genuine full 40-hex SHA-1 identities;
- local object/ref identities remain full content-derived 64-hex SHA-256 values;
- immutable object publication remains the only permitted pre-ref repository mutation;
- no SHA-1 padding, truncation, translation, surrogate SHA-256, or metadata-derived local identity is introduced.

## Tests

`tests/test_phase325.py` adds deterministic fault injection across the mutable publication surface. It verifies that changes to HEAD, packed refs, promisor state, shallow state, target refs, and target reflogs all abort before ref publication; a race injected during the download window is also detected. A separate regression proves that immutable object-store writes remain permitted, and existing promisor bytes remain untouched on the successful path.

The complete inherited test suite remains authoritative for protocol-v2 framing, native Git interoperability, packfile-URI checksum/HTTP behavior, SHA-256 import, connectivity certification, and CAS ref publication.
