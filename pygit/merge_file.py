"""Standalone three-way file merge plumbing.

The helpers in this module deliberately operate on bytes and filesystem paths
without requiring a pygit repository.  Clean exact/one-side-unchanged cases
remain safe for arbitrary binary data; when both sides changed, automatic
line merging is limited to lossless UTF-8 text so content is never rewritten
through replacement characters.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union


PathLike = Union[str, Path]
Edit = Tuple[int, int, List[str]]


@dataclass(frozen=True)
class MergeFileResult:
    """Result of a three-way file merge."""

    data: bytes
    conflicts: int

    @property
    def clean(self) -> bool:
        return self.conflicts == 0


def _decode_mergeable(data: bytes) -> List[str]:
    if b"\x00" in data:
        raise ValueError("merge-file cannot line-merge binary data containing NUL bytes")
    try:
        return data.decode("utf-8", errors="strict").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise ValueError("merge-file cannot line-merge non-UTF-8 binary data") from exc


def _edits(base: Sequence[str], side: Sequence[str]) -> List[Edit]:
    result: List[Edit] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, base, side).get_opcodes():
        if tag != "equal":
            result.append((i1, i2, list(side[j1:j2])))
    return result


def _marker(char: str, size: int, label: Optional[str] = None) -> str:
    suffix = f" {label}" if label else ""
    return char * size + suffix + "\n"


def merge_file_data(
    current: bytes,
    base: bytes,
    other: bytes,
    *,
    labels: Tuple[str, str, str] = ("current", "base", "other"),
    style: str = "merge",
    marker_size: int = 7,
) -> MergeFileResult:
    """Three-way merge *current* and *other* relative to *base*.

    ``style`` accepts ``"merge"`` or ``"diff3"``.  The return value always
    contains the would-be merged bytes; conflicted results include conflict
    markers and report how many conflict regions were emitted.
    """
    if style not in {"merge", "diff3"}:
        raise ValueError("merge-file style must be 'merge' or 'diff3'")
    if marker_size < 1:
        raise ValueError("merge-file marker size must be at least 1")
    if len(labels) != 3:
        raise ValueError("merge-file requires exactly three labels")

    # Exact and one-side-unchanged cases are byte-preserving and therefore safe
    # even for binary files.
    if current == other:
        return MergeFileResult(current, 0)
    if current == base:
        return MergeFileResult(other, 0)
    if other == base:
        return MergeFileResult(current, 0)

    base_lines = _decode_mergeable(base)
    current_lines = _decode_mergeable(current)
    other_lines = _decode_mergeable(other)
    current_edits = _edits(base_lines, current_lines)
    other_edits = _edits(base_lines, other_lines)

    result: List[str] = []
    conflicts = 0
    base_pos = 0
    ci = oi = 0

    while ci < len(current_edits) or oi < len(other_edits):
        current_edit = current_edits[ci] if ci < len(current_edits) else None
        other_edit = other_edits[oi] if oi < len(other_edits) else None

        if current_edit and other_edit:
            if current_edit[1] <= other_edit[0]:
                result.extend(base_lines[base_pos:current_edit[0]])
                result.extend(current_edit[2])
                base_pos = current_edit[1]
                ci += 1
                continue
            if other_edit[1] <= current_edit[0]:
                result.extend(base_lines[base_pos:other_edit[0]])
                result.extend(other_edit[2])
                base_pos = other_edit[1]
                oi += 1
                continue
            if (
                current_edit[0] == other_edit[0]
                and current_edit[1] == other_edit[1]
                and current_edit[2] == other_edit[2]
            ):
                result.extend(base_lines[base_pos:current_edit[0]])
                result.extend(current_edit[2])
                base_pos = current_edit[1]
                ci += 1
                oi += 1
                continue

            conflicts += 1
            start = min(current_edit[0], other_edit[0])
            end = max(current_edit[1], other_edit[1])
            result.extend(base_lines[base_pos:start])
            result.append(_marker("<", marker_size, labels[0]))
            result.extend(current_edit[2])
            if style == "diff3":
                result.append(_marker("|", marker_size, labels[1]))
                result.extend(base_lines[start:end])
            result.append(_marker("=", marker_size))
            result.extend(other_edit[2])
            result.append(_marker(">", marker_size, labels[2]))
            base_pos = end
            ci += 1
            oi += 1
            continue

        if current_edit:
            result.extend(base_lines[base_pos:current_edit[0]])
            result.extend(current_edit[2])
            base_pos = current_edit[1]
            ci += 1
            continue

        assert other_edit is not None
        result.extend(base_lines[base_pos:other_edit[0]])
        result.extend(other_edit[2])
        base_pos = other_edit[1]
        oi += 1

    result.extend(base_lines[base_pos:])
    return MergeFileResult("".join(result).encode("utf-8"), conflicts)


def merge_file(
    current_path: PathLike,
    base_path: PathLike,
    other_path: PathLike,
    *,
    labels: Optional[Tuple[str, str, str]] = None,
    style: str = "merge",
    marker_size: int = 7,
    write_current: bool = True,
) -> MergeFileResult:
    """Merge three files, optionally replacing the current file in place."""
    current_file = Path(current_path)
    base_file = Path(base_path)
    other_file = Path(other_path)
    resolved_labels = labels or (str(current_file), str(base_file), str(other_file))

    result = merge_file_data(
        current_file.read_bytes(),
        base_file.read_bytes(),
        other_file.read_bytes(),
        labels=resolved_labels,
        style=style,
        marker_size=marker_size,
    )
    if write_current:
        current_file.write_bytes(result.data)
    return result
