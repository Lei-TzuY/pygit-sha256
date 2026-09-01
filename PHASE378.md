# Phase378: Integrate strict loose-object validation with pinned publication

Phase378 cleanly combines the two exact-green Phase373 durability siblings:

- Phase375's bounded, fsck-grade validation of an already-visible loose object;
- Phase376's retained-descriptor inode correlation for a newly published loose object.

## Why integration is required

Phase375 and Phase376 both strengthened `pygit.durable_object_store`, but they were created independently from the same Phase373 base. Leaving them as permanent siblings would force later durability work to choose between two incomplete halves of the same success-after-durability contract.

The important cross-feature case is a concurrent replacement after our own atomic publication. Phase376 correctly notices that the final pathname no longer names the inode that our transaction fsynced and then asks the existing-object certification path whether the competing winner can be trusted. Without Phase375 integrated, that winner was checked with one-shot `zlib.decompress()`, which could accept a correct first zlib stream followed by trailing garbage.

Phase378 closes that composition gap.

## Integrated contract

For an already-visible candidate:

1. open without following symlinks and make the descriptor non-inheritable;
2. pin the regular-file inode;
3. stream-decompress in bounded chunks and compare exactly with the requested Git object envelope;
4. reject truncation, trailing bytes, concatenated streams, output beyond the expected envelope, and decoder no-progress states;
5. fsync the exact validated inode;
6. correlate the live pathname with that inode before and after fanout/root directory fences.

For a new publication:

1. create a same-directory temporary;
2. keep its descriptor open and non-inheritable;
3. write/flush/fsync the compressed Git object envelope;
4. pin the temporary inode;
5. atomically replace the content-addressed pathname;
6. fence the fanout and objects-root namespaces;
7. return success only if the live path still names the exact inode we published;
8. if another writer replaced it, accept the winner only through the strict existing-object contract above, otherwise retry atomic publication.

Thus both entry paths now converge on the same rule: visibility alone is never sufficient for write-side success.

## Regression coverage

Phase378 retains Phase375's original focused tests and all inherited Phase376 tests, then adds cross-feature races that prove:

- a competing winner containing the correct first zlib stream plus trailing garbage is rejected and repaired by a second atomic publication;
- a competing concatenated zlib stream is likewise rejected and repaired;
- an exact competing stream remains acceptable after strict inode-aware certification;
- both the initial existing-object fast path and race-winner certification receive the exact Git object envelope, not a derived identifier surrogate.

The native Phase375 differential remains present: SHA-256 Git `fsck --strict` must reject trailing garbage in a loose object.

## SHA-256-native invariants

The local object pathname remains the genuine 64-hex SHA-256 of `<type> <size>\0<payload>`. Validation compares directly against that exact envelope. Remote/native identities elsewhere remain genuine complete 40-hex SHA-1 where Git interoperability requires them. No padding, truncation, textual-object-id rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- actual `main` remains `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase376 / PR #353 head `98a041d2381844ef9213c75523a13bd2125c59ce`;
- Phase376 Tests #3158: Python 3.9 / 3.13 both 2702 passed, Git 2.55.0;
- Phase375 / PR #352 is the sibling whose strict validator/tests/docs are integrated here;
- Phase377 was already occupied by unrelated clone `--no-tags` work;
- Phase378 was collision-checked immediately before creation and was free.

This phase intentionally remains a stacked, open, unmerged pull request.
