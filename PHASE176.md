# Phase 176 — Push remote groups

Phase 176 adds Git-style remote groups to `pygit push` while preserving the
existing SHA-256-native object/ref model and the Phase 166–175 push stack.

## User-facing behavior

A group is configured through the Git-style `remotes.<group>` key:

```bash
pygit config remotes.all-remotes "origin backup staging"
pygit push all-remotes main
```

The group value is a whitespace-separated ordered list of already-configured
named remotes.  Group order and duplicate entries are preserved.  Empty groups
and unknown member names are rejected before transport starts.

A group push is deliberately not a new transport mode.  It is equivalent to
running the same ordinary push once for every member remote, in order.  This
means each member independently evaluates:

- explicit refspecs and selection flags;
- `remote.<name>.push`;
- `remote.<name>.mirror`;
- `push.default`;
- negative refspecs and prune planning;
- follow-tags selection;
- force / force-with-lease / force-if-includes;
- push options;
- set-upstream / auto-setup behavior.

A mirror-configured member therefore cannot accidentally turn the other group
members into mirror pushes.

## Failure semantics

Member pushes are independent.  If one member fails, pygit reports that failure
and continues with later remotes.  The group command returns a non-zero status
when any member failed.

Parser-level member-specific conflicts are treated the same way: the failing
member does not prevent later members from being attempted.

`--atomic` is rejected for a remote group.  Atomic receive-pack only protects
one connection to one remote; it cannot make several independent repositories
one transaction.

## Git compatibility

Current Git documentation defines a remote group as a named list configured by
`remotes.<name>` and describes `git push <group>` as sequential ordinary pushes
to its members.  The same documentation requires per-remote push configuration
to be evaluated independently, rejects `--atomic` for group pushes, and says a
member failure does not stop later member pushes while the overall exit code is
non-zero.

The container's native Git 2.47.3 predates push remote-group support, so Phase
176 uses the current upstream Git documentation as the compatibility target
rather than treating the older local binary's lack of the feature as normative.

## SHA-256-native design

Remote groups only affect destination selection and orchestration.  They do not
change object IDs, object serialization, refs, index data, pack conversion, or
the SHA-256-native -> native SHA-1 smart-HTTP boundary.  Every member delegates
to the same Phase 166–175 planner and transport functions already used by a
single-remote push.

## Regression coverage

`tests/test_phase176.py` covers:

- ordered group parsing and duplicate preservation;
- empty and unknown-member rejection;
- forwarding the same parsed flags/refspecs to every member;
- continuing after an ordinary member failure;
- non-zero aggregate status after any failure;
- rejecting `--atomic` before transport;
- independent default-vs-mirror planning for different members;
- continuing after a member-specific parser/configuration failure.

## Scope boundary

This phase intentionally adds only named remote groups.  Multiple push URLs for
one remote (`remote.<name>.pushurl` / multiple URLs), signed push certificates,
and protocol-v2 send-pack negotiation remain separate future work because they
have different transport and failure semantics.
