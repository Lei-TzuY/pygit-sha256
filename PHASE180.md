# Phase 180 — Remote default-branch symbolic refs

Phase 180 adds Git-compatible `remote set-head` support on top of Phase 179's synchronized remote lifecycle/configuration layer.

## User-visible behavior

The modern remote porcelain now accepts:

```text
pygit remote set-head <name> <branch>
pygit remote set-head <name> -a|--auto
pygit remote set-head <name> -d|--delete
```

An explicit branch creates `refs/remotes/<name>/HEAD` as a symbolic reference to `refs/remotes/<name>/<branch>`. The destination remote-tracking branch must already exist. Deletion removes only the symbolic `HEAD`; it does not remove any tracking branches.

Explicit set/delete are ref-oriented, matching native Git: they do not require `remote.<name>.*` configuration to exist. If `refs/remotes/<name>/<branch>` exists, the explicit form may point HEAD at it; deleting a missing/unconfigured remote HEAD is a successful no-op. `--auto` is different because it must query the named remote's configured fetch URL.

`--auto` queries the remote's upload-pack advertisement, reads the advertised `HEAD` symref, and points the local remote `HEAD` at the same already-fetched tracking branch. As with native Git, auto mode fails if that tracking branch does not exist locally yet.

After a remote HEAD exists, the remote name itself can resolve through the ref layer. For example, with `origin/HEAD -> origin/main`, the revision `origin` resolves to the same SHA-256 object ID as `origin/main`.

## Ref-store integration

Remote HEAD is represented as a real loose symbolic ref, not as configuration metadata:

```text
refs/remotes/origin/HEAD
    ref: refs/remotes/origin/main
```

The ref store resolves this through the same symbolic-reference machinery used for local `HEAD`. Remote branch enumeration excludes the `HEAD` alias so fetch/prune/branch-selection code does not mistake it for a real remote branch.

Phase 179 remote rename already owns remote-tracking namespace movement. Phase 180 extends that path so moving `refs/remotes/origin/*` to `refs/remotes/upstream/*` also rewrites the symbolic HEAD target from `refs/remotes/origin/main` to `refs/remotes/upstream/main`.

## Git compatibility checks

Current upstream `git-remote` documentation specifies that:

- `remote set-head <name> <branch>` points `refs/remotes/<name>/HEAD` at the named tracking branch;
- `-a/--auto` queries the remote HEAD and uses the same branch;
- explicit and auto modes require the corresponding remote-tracking branch to exist already;
- `-d/--delete` removes the symbolic ref only.

Native Git 2.47.3 local probes additionally confirmed:

- explicit set-head succeeds silently;
- auto mode prints `<name>/HEAD set to <branch>`;
- delete succeeds silently;
- explicit selection of a missing tracking branch exits non-zero;
- deleting HEAD leaves the tracked branch intact;
- `remote set-head missing -d` succeeds even without remote configuration;
- a manually existing `refs/remotes/<name>/<branch>` is sufficient for explicit set-head even when that name has no remote config.

## SHA-256-native design

This phase adds only a symbolic ref whose resolved value is an existing local SHA-256 tracking ref. It does not change object serialization, object IDs, the index, pack conversion, native SHA maps, fetch object conversion, or the SHA-256-native to native SHA-1 smart-HTTP boundary.

## Regression coverage

`tests/test_phase180.py` covers:

- explicit symbolic HEAD creation;
- remote-name revision shorthand;
- rejection of unfetched destination branches without mutation;
- idempotent deletion that preserves tracking refs;
- ref-oriented explicit/delete operation without remote config;
- advertised-HEAD auto selection;
- auto rejection when the advertised branch is not fetched;
- remote rename target rewriting;
- native-compatible CLI output for explicit, auto, and delete modes.
