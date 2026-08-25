# Phase 62: merge-file plumbing

Phase 62 adds standalone three-way file merge plumbing that does not require a `.pygit` repository.

## CLI

```bash
pygit merge-file CURRENT BASE OTHER
pygit merge-file --stdout CURRENT BASE OTHER
pygit merge-file --diff3 CURRENT BASE OTHER
pygit merge-file --marker-size=10 CURRENT BASE OTHER
pygit merge-file -L ours -L base -L theirs CURRENT BASE OTHER
```

By default, the merged result replaces `CURRENT`. `--stdout` leaves all input files untouched and writes the result to standard output. A clean merge exits 0; a conflicted merge emits conflict markers and returns the number of conflict regions, capped at 127 for process exit status compatibility.

`--diff3` includes the common-base text inside each conflict. `-L` overrides the current/base/other labels in order, and `--marker-size` controls the marker width.

## Python API

```python
from pygit import merge_file, merge_file_data

result = merge_file_data(current_bytes, base_bytes, other_bytes)
if result.clean:
    print(result.data)

result = merge_file("ours.txt", "base.txt", "theirs.txt", write_current=False)
```

`MergeFileResult` contains the merged bytes and conflict-region count.

## Binary safety

Exact agreement and one-side-unchanged cases are resolved byte-for-byte, even for arbitrary binary payloads. If both sides changed, line merging is attempted only when all three inputs are lossless UTF-8 text and contain no NUL bytes. NUL-containing or invalid UTF-8 inputs fail before `CURRENT` is rewritten, avoiding replacement-character corruption.

## Compatibility boundary

This is a focused educational subset of `git merge-file`. It implements in-place/stdout output, merge/diff3 conflict presentation, custom labels, and configurable marker size. Strategy flags such as `--ours`, `--theirs`, `--union`, and `--zdiff3` are intentionally not claimed yet.
