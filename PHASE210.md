# Phase 210: fetch --update-shallow safety

Phase210 adds Git-style shallow-source safety to protocol-v2 fetch.

## Behavior

`pygit fetch` now refuses refs whose protocol-v2 response would change the local
`.pygit/shallow` boundary unless `--update-shallow` is explicitly supplied.
Like native Git, the default refusal is warning-only and the fetch command can
still exit successfully; the rejected refs are not installed.

With:

```bash
pygit fetch --update-shallow origin
```

pygit advertises its current shallow boundary to the remote, accepts returned
`shallow-info`, imports genuinely truncated commit graphs with the stable
foreign-parent importer, and writes the resulting boundary using local SHA-256
object IDs.

Native Git SHA-1 object IDs remain confined to protocol-v2 negotiation and the
existing native-map interoperability boundary.

## Compatibility grounding

Git documents `--update-shallow` as the explicit opt-in required when fetching
from a shallow repository would require changing `.git/shallow`.

A local Git 2.47.3 probe with a depth-1 source repository confirmed the exact
status behavior: ordinary `git fetch` returned status 0 but warned that the
remote-tracking ref was rejected because shallow roots were not allowed to be
updated; repeating the fetch with `--update-shallow` installed the ref and
created the local shallow boundary.

Phase210 currently requires protocol v2 and one named remote. To keep the safety
model unambiguous in this first phase, it does not combine `--update-shallow`
with depth/deepen/unshallow selectors, shallow-since/exclude, multi-remote,
prefetch, refetch, or negotiate-only modes. Those existing modes already own
separate shallow/transport semantics and can be reconciled deliberately later.

The standard `--` option terminator remains authoritative: a literal
`--update-shallow` after `--` is treated as a refspec token.

## Verification focus

Regression coverage checks option parsing, native-compatible warning-only
refusal, current-boundary SHA-256 -> native SHA-1 translation, command-scope
restoration, protocol-v2 enforcement, default-remote insertion, and
incompatible-mode rejection.
