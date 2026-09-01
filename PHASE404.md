# Phase404 — preserve ordinary `check-ref-format --normalize` trailing-slash rejection

Phase404 closes the same normalization boundary for ordinary refnames that Phase403 closed for `--refspec-pattern`.

## Compatibility rule

Git documents `--normalize` as removing leading `/` characters and collapsing adjacent `/` characters *between* refname components. The normalized result must still be a valid refname. Therefore a trailing slash remains an empty final component and must not become valid merely because normalization is enabled.

Native Git behavior used for the differential regression:

- `git check-ref-format --normalize //refs//heads//topic` succeeds and prints `refs/heads/topic`.
- `git check-ref-format --normalize refs/heads/topic/` fails.
- repeated internal slashes followed by a trailing slash also fail.
- slash-only inputs fail.
- deprecated `--print` inherits the same behavior because it aliases `--normalize`.

## Implementation

The guard now lives in the shared `check_ref_format()` validator before `normalize_refname()` can erase the empty final component. This makes both direct callers and the CLI obey the same rule. Phase403's refspec-specific guard remains valid and unchanged.

No other normalization behavior changes: leading slashes are removed and repeated internal slashes are collapsed as before.

## SHA-256-native boundary

This phase changes textual reference-name validation only. It creates or rewrites no object IDs, refs, reflogs, native mappings, `FETCH_HEAD` records, packfiles, or promisor state. Local object identity remains genuine content-derived 64-hex SHA-256; native Git interoperability identities remain genuine complete 40-hex SHA-1 where required.

## Coordination

- exact base: Phase403 head `878dd1873446d673940ff03db87288fcac6f7d3f`
- Phase403 GitHub Actions Tests #3227 completed successfully before Phase404 was created
- `phase404` was collision-checked and free
- active clone, init, protocol-v2, and loose-object durability stacks were left untouched
