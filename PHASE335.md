# Phase 335: Transition an unborn clone through its first concrete fetch

Phase331 can now clone a protocol-v2 remote whose `HEAD` is explicitly unborn.
That creates a real local repository with a symbolic unborn branch, but no object
or remote-tracking ref exists yet. Phase335 defines what happens when the remote
later publishes its first commit and the user runs `pygit fetch`.

## Native Git compatibility

Local Git 2.47.3 SHA-256 probes established two distinct behaviors after an empty
remote's default branch `topic/empty` receives its first commit.

### Default empty clone

The clone already has the wildcard refspec:

`+refs/heads/*:refs/remotes/origin/*`

A later `git fetch origin`:

- fetches the new commit into `FETCH_HEAD`;
- creates `refs/remotes/origin/topic/empty`;
- leaves local `refs/heads/topic/empty` unborn;
- does not check out or otherwise resolve the local branch.

### Empty `--single-branch` clone

Native Git intentionally created the empty clone without a
`remote.origin.fetch` refspec. A later `git fetch origin` still uses the current
unborn branch's upstream metadata to request `refs/heads/topic/empty`, but it has
no configured destination.

Therefore the first fetch:

- imports the commit and records it in `FETCH_HEAD`;
- does **not** create `refs/remotes/origin/topic/empty`;
- leaves local `refs/heads/topic/empty` unborn;
- preserves the missing `remote.origin.fetch` configuration.

`git fetch --all` observes the same source-only behavior for this remote.

## Implementation

New module: `pygit/fetch_unborn_transition.py`.

The public fetch command is wrapped in a command-scoped selector projection. It
reuses the established fetch transport/import/native-map/FETCH_HEAD pipeline and
does not introduce a second fetch implementation.

When a named remote has no configured fetch refspec, Phase335 synthesizes one
**source-only** refspec for the duration of that fetch command only if all of
these conditions are true:

- the current local branch exists symbolically but has no local object tip;
- `branch.<name>.remote` names the selected remote;
- `branch.<name>.merge` is exactly `refs/heads/<name>`;
- Phase331's historical clone metadata still records `<name>` as that remote's
  default branch;
- the remote is not configured as a persistent partial/promisor remote.

A real configured fetch refspec always wins. The projection is restored in a
`finally` block, so callers and Phase182 configuration APIs continue to observe
the persisted empty fetch-refspec list.

Because the synthesized refspec has no destination, ordinary fetch machinery:

1. selects the native remote branch;
2. fetches/imports its object graph;
3. writes the native-SHA-1 ↔ local-SHA-256 map;
4. writes local SHA-256 `FETCH_HEAD` metadata;
5. publishes no local branch and no remote-tracking branch.

The default empty clone's wildcard refspec bypasses this fallback and therefore
continues to create the normal remote-tracking ref.

## Persistent partial clone boundary

Native Git automatically reuses a configured partial-clone filter on later
fetches. A local probe of an empty `--filter=blob:none --single-branch` clone
showed the first concrete fetch sending `filter blob:none` again.

The existing pygit fetch stack enters filtered transport only when the command
explicitly carries `--filter`. Applying Phase335's ordinary source-only fallback
to a persistent promisor remote could therefore over-fetch content that should
remain promised.

Phase335 deliberately fails closed by not synthesizing the unborn fallback when
`remote.<name>.partialCloneFilter` or the promisor flag is present. A later phase
can compose the same source selection with the persistent filter transport.

## SHA-256-native invariants

No hash-domain shortcut is introduced.

- wire/ref advertisement identities remain genuine full 40-hex remote SHA-1;
- imported local objects receive only content-derived 64-hex SHA-256 identities;
- the native map records the validated full SHA-1 ↔ full SHA-256 pair;
- `FETCH_HEAD` contains the repository-native 64-hex SHA-256 object id;
- the local unborn branch receives no zero id and no synthetic object id;
- no SHA-1 is padded, truncated, or treated as a surrogate SHA-256.

## Coordination

- actual `main` at Phase335 start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase331 / PR #308 head
  `40dacfe1dd2f05d6fb67864d291523f3add21036`;
- Phase331 authoritative Tests #2826: Python 3.9 / 3.13 both 2374 passed,
  runner Git 2.55.0;
- Phase332 and Phase333 were already occupied by the object-map/packfile-URI
  lines;
- Phase334 was observed free and then occupied by
  `phase334-integrate-incremental-packfile-uri-fetch` before this work could
  claim it, so this independent unborn lifecycle phase moved to Phase335;
- no sibling packfile-URI branch is modified.

## Regression coverage

`tests/test_phase335.py` covers:

- single-branch unborn upstream imported only into native map + `FETCH_HEAD`;
- default wildcard empty clone retaining ordinary remote-tracking publication;
- local branch remaining unborn in both modes;
- persisted empty fetch-refspec state remaining empty;
- resolved branches not activating the fallback;
- strict matching of remote, merge ref, and historical default branch;
- persistent partial/promisor remotes excluded from the ordinary fallback;
- command-scoped selector restoration after exceptions;
- a native Git SHA-256 empty-clone → first-commit → fetch differential test for
  both default and `--single-branch` clones.

The local execution container still cannot reliably clone this GitHub repository,
so exact-head GitHub Actions Python 3.9 / 3.13 remains the authoritative full-suite
gate for Phase335.