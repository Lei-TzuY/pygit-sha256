# Phase 181 — Clone/fetch remote HEAD lifecycle

Phase 181 connects Phase180's remote default-branch symbolic refs to clone and configured fetch behavior.

## Clone behavior

The modern clone path now materializes the same core remote metadata Git relies on after cloning:

```text
remote.origin.url = <url>
remote.origin.fetch = +refs/heads/*:refs/remotes/origin/*
```

For a single-branch clone the fetch mapping is narrowed to the selected branch:

```text
remote.origin.fetch = +refs/heads/dev:refs/remotes/origin/dev
```

The initial remote HEAD alias reflects the server's real default branch, not merely the branch selected with `-b`:

- normal clone, remote HEAD = `main` -> `origin/HEAD -> origin/main`
- `clone -b dev` while remote HEAD = `main` -> checkout `dev`, but `origin/HEAD -> origin/main`
- `clone --single-branch` with remote HEAD = `main` -> only `origin/main`, with `origin/HEAD -> origin/main`
- `clone --single-branch -b dev` while remote HEAD = `main` -> only `origin/dev`; no `origin/HEAD`, because its real target was not fetched

Single-branch finalization also removes any extra tracking branches left by the historical initial transport path.

## Depth and single-branch

Current Git documents `--depth` as implying `--single-branch` unless `--no-single-branch` is supplied. The modern clone grammar now exposes the same override:

```text
pygit clone --depth 1 URL
pygit clone --depth 1 --no-single-branch URL
```

Non-positive depth values are rejected before the clone starts.

## Configured fetch

`pygit fetch` now goes through a configured fetch layer instead of directly calling the legacy all-branches `Repository.fetch()` implementation.

The selector honors branch source selection from `remote.<name>.fetch`, including exact, wildcard, and negative source patterns. This makes a clone-generated exact single-branch refspec operational across later fetches instead of being configuration-only metadata.

The advertisement pseudo-ref `HEAD` is used only to identify the server default branch and is not itself treated as a transfer/update target. Tags retain pygit's existing automatic tag-import behavior in this phase.

`pygit pull` uses the same configured fetch layer before merging its already-established upstream.

## Remote HEAD stability across fetch

A normal fetch deliberately does **not** rewrite `refs/remotes/<remote>/HEAD` when the server changes its default branch. This matches native Git: the alias remains where the user/clone last placed it until:

```text
pygit remote set-head <remote> --auto
```

The fetch still records the newly advertised default branch in pygit's historical remote metadata for compatibility with older Repository APIs.

## Git compatibility checks

Current Git 2.54 documentation states that clone creates remote-tracking refs and `remote.origin.url` / `remote.origin.fetch`, that `--single-branch` narrows subsequent fetches to the selected branch, and that `--depth` implies `--single-branch` unless overridden.

Native Git 2.47.3 probes confirmed:

- a normal clone creates `origin/HEAD` pointing to the remote's active branch;
- `clone -b dev` does not change `origin/HEAD` away from the server default branch;
- `clone --single-branch` persists an exact fetch refspec for the selected branch;
- `clone --single-branch -b dev` omits `origin/HEAD` when the server default branch is not fetched;
- changing the server HEAD and running plain `git fetch` does not retarget an existing `origin/HEAD`;
- `git remote set-head origin -a` performs that retarget explicitly.

## SHA-256-native design

All tracking refs continue to store pygit's internal SHA-256 object IDs. Phase181 changes only remote metadata, ref selection, and symbolic aliases. Smart-HTTP object conversion and the native SHA-1 boundary remain unchanged.

## Regression coverage

`tests/test_phase181.py` covers:

- full clone with a non-default checkout branch while retaining the server default remote HEAD;
- default and non-default single-branch clone finalization;
- exact single-branch fetch mapping persistence;
- exclusion of advertisement `HEAD` and unselected branch heads from configured fetch targets;
- plain fetch preserving an existing remote HEAD after the server default changes;
- `--depth` implying single-branch and `--no-single-branch` overriding that implication.
