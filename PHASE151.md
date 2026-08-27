# Phase 151 — status porcelain v2

Phase 151 adds Git-style `status --porcelain=v2` output on top of Phase 150's authoritative multi-stage index conflict model. The goal is a stable machine-readable status protocol for editors, scripts, and tooling without duplicating working-tree classification logic.

## Commands

```bash
pygit status --porcelain=v2
pygit status --porcelain=v2 --branch
pygit status --porcelain=v2 --ignored
pygit status --porcelain=v2 -z
```

`--porcelain=v1` remains supported. Phase 151 also adds `-z` NUL termination for both porcelain versions.

## Porcelain v2 records

Ordinary tracked changes use Git's type-1 record layout:

```text
1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
```

Unchanged sides are represented by `.` rather than a space. `mH`, `mI`, and `mW` are HEAD/index/worktree modes; `hH` and `hI` are HEAD/index object IDs. Because pygit is SHA-256-native, object IDs and zero object placeholders are 64 hexadecimal characters.

Unmerged paths use the type-`u` layout:

```text
u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
```

The seven conflict XY states introduced in Phase 150 are preserved, while modes and object IDs come directly from stages 1, 2, and 3. Missing stages use `000000` and the 64-zero object ID. Non-submodule entries use `N...`.

Untracked and ignored records use `? <path>` and `! <path>` respectively. Ignored records remain opt-in through `--ignored`.

## Branch headers

With `--branch`, porcelain v2 emits the extensible Git-style headers:

```text
# branch.oid <oid> | (initial)
# branch.head <branch> | (detached)
# branch.upstream <upstream>
# branch.ab +<ahead> -<behind>
```

Upstream lines are emitted only when tracking information exists. Initial repositories use `(initial)` for `branch.oid`.

## Path and framing behavior

Normal line-oriented output C-quotes pathnames that require escaping. `-z` instead emits raw pathnames and terminates every header/record with NUL, making newline-containing paths unambiguous. Porcelain v1 gains the same NUL record termination while keeping its existing XY records.

## Compatibility boundary

This phase implements the porcelain-v2 record families that pygit's current status engine can produce: ordinary tracked changes, unmerged changes, untracked/ignored paths, branch/upstream headers, and NUL framing. Rename/copy type-2 records are intentionally not synthesized because the status engine does not currently perform rename detection; adding rename scoring is a separate semantic feature rather than a presentation shortcut.

The operation is read-only and does not modify the index, refs, object store, or worktree.
