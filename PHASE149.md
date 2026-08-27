# Phase 149 — checkout-index creation/stat/quiet controls

Phase 149 closes three remaining `checkout-index` compatibility gaps after the
stage-aware and temporary-export work in Phases 126 and 148.

## Installed CLI

```bash
pygit checkout-index --no-create path
pygit checkout-index --index path
pygit checkout-index --quiet path
```

The opposite forms are also accepted:

```bash
pygit checkout-index --create path
pygit checkout-index --no-index path
pygit checkout-index --no-quiet path
```

## `--no-create`

`-n` / `--no-create` skips a selected entry when its final checkout target does
not already exist. The target includes any active `--prefix`.

If the target does exist, ordinary overwrite protection still applies:
`--force` is required to replace it. The skip decision happens before object
materialization, so an absent target can be skipped without reading its backing
object.

`--temp` and `--stage=all` keep their Phase 148 behavior; native Git treats
`--no-create` as irrelevant to temporary export, and pygit does the same.

## `--index`

`-u` / `--index` refreshes the selected index entry's readable stat cache after
a normal checkout. Pygit's index stores `size` and `mtime`, so those are updated
from the materialized path after a successful write.

This applies to stage 0 and explicit conflict stages 1/2/3. Other stages for the
same unmerged path are preserved.

Native Git does not use a redirected `--prefix` checkout to refresh the tracked
path's index stat data, and temporary export likewise does not refresh it.
Pygit preserves that distinction.

## `--quiet`

`-q` / `--quiet` suppresses the expected warnings for:

- a requested path/pathspec not present at the selected index stage;
- an existing target that would require `--force`.

The command still exits non-zero. Structural or object-store failures are not
hidden; `--quiet` is not a general error-suppression switch.

## Design

The historical `checkout_index()` API remains unchanged. Phase 149 adds a
focused `checkout_index_controlled()` compatibility layer used by the installed
CLI, keeping Phase 148 temporary extraction and older internal callers stable.

## Regression coverage

`tests/test_phase149.py` covers:

- absent-target skipping and existing-target force behavior;
- `--create` overriding a prior `--no-create`;
- stage-0 and stage-2 stat refresh;
- `--no-index` overriding `--index`;
- no stat refresh for `--prefix` or temporary export;
- quiet missing/existing warnings with preserved failure status;
- object-store failures remaining visible under `--quiet`;
- installed help for the new control surface.
