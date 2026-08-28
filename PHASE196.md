# Phase 196 — Fetch refetch negotiation

Phase196 adds Git-style `fetch --refetch` on top of Phase195.

## Behavior

`pygit fetch --refetch` deliberately disables local `have` negotiation for the
selected fetch object graph. Even when a selected remote tip already has a
native-SHA mapping locally, pygit asks the smart-HTTP server for the graph as a
fresh fetch would and re-imports it into the SHA-256-native object store.

The option composes with configured remotes, explicit refspec fetches, direct
HTTP(S) URL fetches, `--prefetch`, `--multiple`, `--all`, remote groups,
`--atomic`, `--force`, and Phase192 `--dry-run`. A token after the standard `--`
option terminator remains a literal refspec and does not activate refetch mode.

## Git compatibility

The upstream `git-fetch` documentation defines `--refetch` as fetching all
objects as a fresh clone would instead of negotiating to avoid objects already
present locally. pygit implements the relevant transport behavior by sending an
empty `have` set through its protocol-v0 smart-HTTP boundary.

Git also documents `--refetch` as useful for reapplying changed partial-clone
filters. pygit does not yet implement partial-clone `--filter`, so Phase196 does
not claim that part of the feature; it provides the no-have refetch semantics
that are meaningful for pygit's current transport.

## SHA-256-native design

The policy changes only negotiation. Remote Git object names remain native
SHA-1 at the smart-HTTP boundary, while imported objects and repository-visible
refs remain pygit's 64-hex SHA-256 identities. Existing native-SHA maps are
preserved and refreshed by the normal importer rather than discarded.

## Tests

`tests/test_phase196.py` verifies that refetch:

- transfers even when the selected tip is already known;
- sends an empty `have` set;
- preserves and refreshes native-SHA bookkeeping;
- patches configured, explicit, and direct fetch import seams only for the
  command scope and restores them after success or exceptions;
- strips the CLI-only `--refetch` token before the established fetch parser;
- respects `--` option termination; and
- composes with the dry-run repository sandbox.
