# Phase 81 — `show-ref --exclude-existing` stdin filter

Phase 81 completes the script-facing `show-ref` mode family started in Phases 79–80 by adding the inverse, stdin-driven ref filter.

## CLI

```bash
printf 'refs/heads/main\nrefs/heads/new\n' | pygit show-ref --exclude-existing
printf 'refs/heads/a\nrefs/tags/v1\n' | pygit show-ref --exclude-existing=refs/heads/
```

`--exclude-existing[=<pattern>]` is a standalone mode. The optional pattern is accepted only in the documented attached `=...` form and performs a literal head-match against the parsed refname. Positional ref arguments and listing/formatting/verification/existence options are rejected. Empty stdin is valid and produces empty stdout without warnings.

For every stdin line the filter:

1. preserves an arbitrary prefix and the original LF/CRLF line ending;
2. strips a trailing `^{}` from the line/refname;
3. applies the optional prefix head-match;
4. validates the final refname with the existing `check-ref-format` rules;
5. skips refs that already exist in loose or packed local storage;
6. emits the remaining line unchanged apart from the `^{}` stripping.

Malformed input refnames are non-fatal: they produce a warning on stderr and are skipped. Input outside the optional prefix is ignored before refname validation, matching the filtering order of Git's documented semantics.

## Storage semantics and safety

Existence is storage-oriented, matching Phase 80 `show-ref --exists`: the target object is not resolved. Therefore dangling symbolic loose refs and packed-only refs with unavailable objects are still considered existing and are filtered out.

The local ref inventory is built once per invocation. Packed refs use the strict packed-ref parser; malformed packed storage fails the command rather than being mistaken for absence. Loose ref path names are validated, and filesystem symlinks below `.pygit/refs` are rejected rather than followed outside ref storage.

The filter is read-only. It does not update refs, reflogs, objects, the index, or the worktree.

## Python API

```python
from pygit import exclude_existing_refs

result = exclude_existing_refs(
    repo,
    [b"refs/heads/main\n", b"refs/heads/new^{}\n"],
    pattern="refs/heads/",
)
print(result.output)
print(result.warnings)
```

`ExcludeExistingResult` contains the filtered output bytes and a tuple of non-fatal malformed-input warning strings.

## Regression coverage

`tests/test_phase81.py` covers dangling loose refs, packed-only refs, missing refs, `^{}` stripping, prefix filtering order, invalid/non-UTF-8 input, CRLF and arbitrary-prefix preservation, CLI option isolation, malformed local packed storage, and symlink fail-closed behavior.
