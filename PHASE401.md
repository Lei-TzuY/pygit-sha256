# Phase401 — Git-compatible `check-ref-format --refspec-pattern`

Phase401 fills a focused plumbing compatibility gap without touching the active clone, init-template, or loose-object durability work.

## Behavior

`pygit check-ref-format --refspec-pattern <refname>` now follows Git's documented exception to ordinary refname validation: the pattern may contain zero or one `*` wildcard. All other refname safety rules remain in force, including rejection of `..`, `@{`, control characters, spaces, `.lock` path components, leading/trailing or repeated slashes (unless normalized), and one-level names unless `--allow-onelevel` is supplied.

`--normalize` composes with `--refspec-pattern` and prints the normalized wildcard pattern. Ordinary `check-ref-format` continues to reject `*` exactly as before.

The implementation deliberately layers the wildcard exception over the established ref validator rather than copying its rule set. A single wildcard is replaced temporarily only for validation, and the normalized original pattern is returned after success.

## Native Git differential

Git 2.47.3 probes used during implementation established these representative results:

- `git check-ref-format --refspec-pattern refs/heads/*` → success
- `git check-ref-format --refspec-pattern foo/bar*/baz` → success
- `git check-ref-format --refspec-pattern foo/*/bar` → success
- `git check-ref-format --refspec-pattern foo/bar*/baz*` → failure
- `git check-ref-format --refspec-pattern foo/bar*baz/` → failure
- `git check-ref-format --refspec-pattern --allow-onelevel foo*` → success
- `git check-ref-format --refspec-pattern --normalize //refs//heads//*` → `refs/heads/*`

The CI regression repeats the acceptance/rejection matrix against the Git available on the runner.

## SHA-256-native boundary

This phase validates refname syntax only. It creates or rewrites no objects, refs, reflogs, mappings, `FETCH_HEAD`, packfiles, or repository metadata. Local object identity therefore remains genuine content-derived 64-hex SHA-256, while remote/native SHA-1 identities remain genuine complete 40-hex values wherever interoperability requires them. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

Phase401 was created directly from the current `main` commit after checking recent PRs and branch namespaces. Phase386 was already reserved for init template-directory work, while Phase400 was active clone-tag work, so this phase intentionally uses the independent reference-plumbing surface.
