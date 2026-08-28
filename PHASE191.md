# Phase 191 — fetch force semantics

Phase191 adds Git-style `fetch -f/--force` on top of Phase190 and tightens local branch destination safety.

## Behavior

`pygit fetch -f` globally permits local ref updates that Git allows to be forced. The flag is propagated through configured named remotes, explicit command-line refspecs, direct HTTP(S) URL fetches, `--multiple`, `--all`, remote groups, and `fetch.all=true` orchestration.

A leading `+` on an individual fetch refspec continues to force only that mapping. Global `--force` is combined with the per-refspec force bit rather than replacing it.

The main observable cases are:

- differing existing tags are rejected normally and may be replaced with `--force`;
- non-fast-forward local branch destinations are rejected normally and may be updated with `--force`;
- remote-tracking refs retain their existing configured force behavior;
- no amount of forcing permits a non-commit object to be written beneath `refs/heads/*`.

## Git compatibility

Current Git fetch documentation defines `-f/--force` as overriding the ordinary local ref update checks that can also be overridden by a leading `+` in a refspec. It separately preserves the invariant that `refs/heads/*` accepts only commit objects, even under force.

Native Git 2.47.3 probes confirmed:

- a non-fast-forward explicit local branch destination fails without force and succeeds with `--force`;
- retargeting an existing tag fails without force and succeeds with `--force`;
- fetching a tag that points to a blob into `refs/heads/*` still fails with `--force` because the target is not a commit.

## Architecture

The CLI forwards a single global force bit into the established configured, porcelain, direct-URL, and multi-remote fetch paths. Destination application ORs that bit with each refspec's existing force marker.

Configured fetch destinations now also support `refs/heads/*` with the same commit-only and fast-forward checks already used by explicit porcelain destinations. This closes a pre-existing consistency gap for custom configured fetch mappings.

## SHA-256-native design

Force changes only local ref-update policy. Local refs and objects continue to use pygit's 64-hex SHA-256 IDs; object serialization, index data, native SHA maps, pack conversion, and the native Git SHA-1 smart-HTTP interoperability boundary are unchanged.

## Regression coverage

`tests/test_phase191.py` covers tag clobber safety, configured force override, the non-commit branch invariant, CLI propagation to configured/explicit/direct fetch, and multi-remote propagation.
