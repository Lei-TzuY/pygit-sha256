# Phase384 — Quiet and explicit storage formats for `pygit init`

Phase384 extends the Phase383 `pygit init` porcelain with two pieces of Git-compatible initialization behavior while keeping pygit's SHA-256-native storage invariant explicit.

## Behavior

`pygit init -q/--quiet` suppresses the normal `Initialized empty ...` / `Reinitialized existing ...` stdout line. Error and warning messages are not redirected. In particular, a quiet reinitialization that supplies a different `-b/--initial-branch` still reports the Phase383 Git-style `ignored --initial-branch=...` warning on stderr.

The initializer also accepts the storage formats that pygit really implements:

- `--object-format=sha256`
- `--ref-format=files`

Those options may be combined with `-q` and `-b/--initial-branch`, and supplying the same formats during reinitialization is safe.

## Fail-before-mutation format boundary

Pygit is deliberately narrower than native Git here. Native Git 2.55 can create SHA-1 or SHA-256 repositories and can select the files or reftable ref backend. Pygit's local object model is unconditionally content-derived SHA-256 and its ref store is the files backend.

Therefore Phase384 rejects unsupported selections before calling `Repository.init()` or creating the target directory. Examples include:

- `--object-format=sha1`
- `--object-format=sha512`
- `--object-format=SHA256`
- `--ref-format=reftable`

Silently accepting any of those would make the porcelain advertise a storage format that the repository implementation cannot honor.

No extra on-disk format marker is needed for the accepted values: the entire pygit repository implementation already has those invariants, and existing `rev-parse --show-object-format` / ref-format behavior reports them from that native design rather than from a user-selectable compatibility flag.

## Quiet implementation

`Repository.init()` is also a long-standing library API whose existing behavior includes printing its informational initialization line. Phase384 does not change that public API. Instead, only the porcelain `run_init()` redirects initializer stdout when `-q/--quiet` is active. The warning channel remains untouched.

## Native Git differential

The regression suite compares a supported invocation against native Git:

`git init -q --object-format=sha256 --ref-format=files -b feature/storage <dir>`

Both implementations must:

- succeed without ordinary stdout/stderr output;
- create the same unborn symbolic HEAD target;
- identify the selected object format as SHA-256 and the selected ref backend as files within their respective storage models.

Additional local tests verify quiet reinitialization warning behavior and fail-before-mutation handling for unsupported formats.

## SHA-256-native invariants

Phase384 does not introduce a SHA-1 local mode. Explicit `--object-format=sha256` confirms the storage invariant rather than switching it. A regression writes a blob after explicit-format initialization and verifies a genuine 64-hex content-derived local object ID.

Remote/native compatibility identities remain genuine complete 40-hex SHA-1 wherever the interoperability layer requires them. No padding, truncation, identifier-text rehashing, surrogate SHA-256, zero OID, or fake ref backend metadata is introduced.

## Coordination

- exact base: Phase383 / PR #357 head `6059606dd8e2eba182450b80e6b6aba7f50a2e99`;
- Phase383 GitHub Actions run `33463794892` completed successfully on Python 3.9 and Python 3.13 with CI Git 2.55.0 before Phase384 was created;
- Phase380–382 remain independent durability/bundle-URI work;
- Phase384 was collision-checked immediately before branch creation and was free.

This phase is intended to remain an open, unmerged stacked pull request.
