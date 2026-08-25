"""Phase 62 tests: standalone merge-file plumbing."""

from pathlib import Path

import pytest

from pygit import merge_file, merge_file_data
from pygit.launcher import _run_merge_file


def test_merge_file_data_combines_non_overlapping_line_edits() -> None:
    result = merge_file_data(
        b"ONE\ntwo\nthree\n",
        b"one\ntwo\nthree\n",
        b"one\ntwo\nTHREE\n",
    )

    assert result.clean
    assert result.conflicts == 0
    assert result.data == b"ONE\ntwo\nTHREE\n"


def test_merge_file_conflict_writes_current_with_labels(tmp_path: Path) -> None:
    current = tmp_path / "current.txt"
    base = tmp_path / "base.txt"
    other = tmp_path / "other.txt"
    current.write_bytes(b"ours\n")
    base.write_bytes(b"base\n")
    other.write_bytes(b"theirs\n")

    result = merge_file(
        current,
        base,
        other,
        labels=("OURS", "BASE", "THEIRS"),
    )

    assert not result.clean
    assert result.conflicts == 1
    assert current.read_bytes() == (
        b"<<<<<<< OURS\n"
        b"ours\n"
        b"=======\n"
        b"theirs\n"
        b">>>>>>> THEIRS\n"
    )


def test_diff3_and_custom_marker_size() -> None:
    result = merge_file_data(
        b"ours\n",
        b"base\n",
        b"theirs\n",
        labels=("LEFT", "ANCESTOR", "RIGHT"),
        style="diff3",
        marker_size=5,
    )

    assert result.conflicts == 1
    assert result.data == (
        b"<<<<< LEFT\n"
        b"ours\n"
        b"||||| ANCESTOR\n"
        b"base\n"
        b"=====\n"
        b"theirs\n"
        b">>>>> RIGHT\n"
    )


def test_stdout_mode_does_not_modify_current(tmp_path: Path, capsysbinary) -> None:
    current = tmp_path / "current.txt"
    base = tmp_path / "base.txt"
    other = tmp_path / "other.txt"
    current.write_bytes(b"ours\n")
    base.write_bytes(b"base\n")
    other.write_bytes(b"theirs\n")

    code = _run_merge_file([
        "--stdout",
        "-L",
        "ours-label",
        "-L",
        "base-label",
        "-L",
        "theirs-label",
        str(current),
        str(base),
        str(other),
    ])

    assert code == 1
    assert current.read_bytes() == b"ours\n"
    output = capsysbinary.readouterr().out
    assert b"<<<<<<< ours-label\n" in output
    assert b">>>>>>> theirs-label\n" in output


def test_binary_content_is_never_lossily_line_merged(tmp_path: Path) -> None:
    current = tmp_path / "current.bin"
    base = tmp_path / "base.bin"
    other = tmp_path / "other.bin"
    current.write_bytes(b"\xff-current\n")
    base.write_bytes(b"\xff-base\n")
    other.write_bytes(b"\xff-other\n")
    before = current.read_bytes()

    with pytest.raises(ValueError, match="non-UTF-8 binary"):
        merge_file(current, base, other)

    assert current.read_bytes() == before


def test_nul_binary_one_side_unchanged_is_byte_preserving() -> None:
    base = b"base\x00payload"
    changed = b"changed\x00payload"

    result = merge_file_data(base, base, changed)

    assert result.clean
    assert result.data == changed


def test_invalid_style_marker_and_label_arguments_fail() -> None:
    with pytest.raises(ValueError, match="style"):
        merge_file_data(b"a", b"b", b"c", style="zdiff3")
    with pytest.raises(ValueError, match="marker size"):
        merge_file_data(b"a", b"b", b"c", marker_size=0)
    with pytest.raises(ValueError, match="three labels"):
        merge_file_data(b"a", b"b", b"c", labels=("a", "b"))  # type: ignore[arg-type]
