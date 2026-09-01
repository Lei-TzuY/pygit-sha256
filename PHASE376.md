# Phase376: Pin new loose-object publication identity

Phase376 closes the new-publication side of the loose-object durability TOCTOU boundary.

## Problem

Phase370 made a new SHA-256 loose-object publication durable as:

`temp write -> file fsync -> atomic replace -> fanout fsync -> objects-root fsync -> success`

Phase373 then added inode-aware certification for an already-existing valid object. The new-publication path, however, still closed its temporary descriptor before `os.replace()` and never correlated the final live pathname with the exact inode whose contents had been fsynced.

With concurrent writers, another process could replace the object pathname after our atomic replace but before our directory fences completed. The namespace fences would then complete successfully, yet the pathname could name an inode whose file data our transaction never fsynced. Returning success in that state would overstate the success-after-durability contract.

## Implementation

The new publication helper now:

1. creates the same-directory temporary with `mkstemp()`;
2. explicitly keeps its descriptor non-inheritable;
3. writes, flushes and fsyncs the compressed Git-compatible object envelope;
4. captures `(st_dev, st_ino)` from the still-open temporary descriptor;
5. atomically replaces the target pathname;
6. fsyncs the fanout directory and primary `objects/` directory;
7. reports success only if the live target still names the exact pinned regular-file inode;
8. if another writer replaced the path, certifies that winner through Phase373's descriptor-pinned validation path; if it is invalid or changes again, retries normal atomic publication.

The retained descriptor is closed on every success and failure path. Temporary cleanup remains best-effort through the existing finally boundary.

## Why this complements Phase373

Phase373 answers: "Can an object that was already visible be trusted and durably certified?"

Phase376 answers: "After publishing a new object, is the object that remains visible still the exact inode whose file contents this transaction made durable?"

Together they give both entry paths the same essential rule: visibility is not enough; success requires a validated or transaction-owned inode plus the applicable namespace durability fences.

## Regression coverage

`tests/test_phase376.py` covers:

- retaining the newly published inode identity across both directory fences;
- accepting a competing valid replacement only after independent Phase373 certification;
- forcing atomic republish when a competing replacement is corrupt;
- preserving post-replace directory-fsync failure propagation;
- keeping the retained publication descriptor non-inheritable.

## Git and SHA-256 compatibility

No object format or identity changes. Loose objects remain zlib-compressed Git object envelopes whose path is the genuine 64-hex SHA-256 of `<type> <size>\0<payload>`. Remote/native interoperability elsewhere continues to use genuine complete 40-hex SHA-1 identities where required. No padding, truncation, textual-id rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

The publication mechanism remains Git-style: same-directory temporary, fully written file, atomic rename into the content-addressed pathname, then namespace durability fences.

## Coordination

- exact base: Phase373 / PR #350 head `085a2994553c94082464310b989e3801d1586a5d`;
- Phase373 GitHub Actions Tests #3143: success, 2697 passed on Python 3.9 and Python 3.13;
- Phase374 and Phase375 were already occupied by parallel work when this phase started;
- Phase376 was collision-checked immediately before branch creation and was free;
- this phase intentionally remains open and unmerged.
