# Phase 145 — independent fsck reference verification

Phase 145 separates reference-database consistency from reachability-root selection. This matters after Phase 144: positional `fsck <object>...` arguments intentionally replace refs/index/reflogs as reachability heads, but they must not silently disable validation of the repository's refs database.

## Commands

```bash
pygit fsck
pygit fsck <object>
pygit fsck --connectivity-only <object>
pygit fsck --no-references <object>
```

Reference verification is enabled by default. `--no-references` skips only the independent consistency pass; it does not change the object heads selected for reachability.

## Checks

The verifier checks the structural reference database independently of object traversal:

- `HEAD` must be a real file when present;
- loose refs must be regular files below a real `.pygit/refs` directory;
- loose and symbolic ref names use the repository's `check-ref-format` validation;
- direct refs must contain a 64-hex SHA-256 object ID;
- symbolic-ref chains are resolved to detect malformed chains and cycles while still allowing unborn targets;
- `packed-refs` is parsed with the existing strict parser, including record, OID, peeled-object, duplicate, and ref-name checks;
- packed/loose file-directory namespace conflicts such as `refs/heads/topic` versus `refs/heads/topic/child` are rejected;
- symlinked reference-store paths are rejected rather than followed.

Object existence remains part of fsck's object/connectivity validation rather than the reference-database consistency pass.

## Explicit-head semantics

Before this phase, `pygit fsck <object>` did not enumerate refs at all because explicit objects correctly replaced the implicit reachability roots. Consequently an unrelated malformed ref could evade fsck.

Now these concerns are separate: the explicit object remains the complete reachability head set, while reference consistency is still checked by default. `--no-references` is the deliberate opt-out, matching current Git's `fsck --[no-]references` model.

Reference errors participate in the normal fsck failure decision and therefore also suppress `--lost-found` materialization, preserving the existing fail-closed recovery policy.

## Python API

```python
from pygit.fsck_references import verify_references

issues = verify_references(repo)
```

The helper returns `FsckIssue` records and does not mutate refs, reflogs, objects, the index, or the worktree.
