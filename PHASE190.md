# Phase 190 — FETCH_HEAD write controls

Phase190 adds Git-style `fetch --write-fetch-head` / `--no-write-fetch-head` behavior on top of Phase189.

## Behavior

`FETCH_HEAD` remains enabled by default. Passing `--no-write-fetch-head` suppresses all FETCH_HEAD writes while still performing the fetch, object import, ref updates, pruning, tag following, and native SHA bookkeeping normally.

The option applies consistently to:

- ordinary configured remote fetches;
- explicit fetch refspec / `--refmap` porcelain;
- direct HTTP(S) URL fetches;
- `--append` invocations;
- `--multiple`, `--all`, `fetch.all=true`, and remote-group orchestration.

`--write-fetch-head` explicitly restores the default behavior. The two switches are mutually exclusive.

## Git compatibility

Current upstream `git-fetch` documents `--write-fetch-head` as the default and states that `--no-write-fetch-head` prevents writing the fetched ref list to `$GIT_DIR/FETCH_HEAD`. Phase190 follows that command-line contract without changing which refs or objects are transferred.

`--append --no-write-fetch-head` therefore performs no FETCH_HEAD mutation: `--append` only changes serialization mode when metadata writing is enabled.

## Architecture

The flag is threaded through the existing Phase184–189 orchestration instead of adding a second fetch path. `fetch_cli` suppresses its configured-fetch metadata helper, while `fetch_porcelain` and `fetch_direct` guard their existing `write_fetch_head()` calls.

Multi-remote orchestration forwards the same write policy to every member, so suppressing FETCH_HEAD cannot accidentally re-enable it on the second or later remote.

## SHA-256-native design

This phase changes only repository-local metadata emission. Local objects and refs remain SHA-256-native, and when FETCH_HEAD is written it continues to contain pygit's 64-hex SHA-256 object IDs. Smart-HTTP native SHA-1 negotiation and pack conversion are unchanged.

## Regression coverage

`tests/test_phase190.py` covers default writing, explicit re-enabling, suppression for configured fetch, explicit porcelain, direct URL fetch, multi-remote propagation, and the interaction with `--append`.
