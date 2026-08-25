"""Phase 69 tests: strict pack-index parsing and show-index plumbing."""

from __future__ import annotations

import hashlib
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from pygit import PackIndexEntry, Repository, parse_index, parse_index_bytes
from pygit.objects import BlobObject
from pygit.pack import PackReader, PackWriter
from pygit.show_index_cli import run_show_index


def _index_bytes(entries: list[tuple[str, int, int]]) -> bytes:
    ordered = sorted(entries, key=lambda item: item[0])
    out = bytearray(b"\xfftOc" + struct.pack(">I", 2))
    counts = [0] * 256
    for oid, _, _ in ordered:
        counts[int(oid[:2], 16)] += 1
    cumulative = 0
    for count in counts:
        cumulative += count
        out.extend(struct.pack(">I", cumulative))
    for oid, _, _ in ordered:
        out.extend(oid.encode("ascii"))
    for _, crc, _ in ordered:
        out.extend(struct.pack(">I", crc))
    for _, _, offset in ordered:
        out.extend(struct.pack(">I", offset))
    out.extend(hashlib.sha256(out).digest())
    return bytes(out)


def _rechecksum(data: bytearray) -> bytes:
    data[-32:] = hashlib.sha256(data[:-32]).digest()
    return bytes(data)


def _sample_pack(tmp_path: Path):
    one = BlobObject(b"one\n")
    two = BlobObject(b"two\n")
    objects = [(one.hash(), one), (two.hash(), two)]
    return PackWriter(objects).write_pack_and_idx(tmp_path, "sample")


def test_parse_index_validates_and_decodes_records() -> None:
    first = "00" + "1" * 62
    second = "ff" + "e" * 62
    parsed = parse_index_bytes(
        _index_bytes([(first, 0x12345678, 12), (second, 0x90ABCDEF, 44)])
    )

    assert parsed.version == 2
    assert parsed.object_count == 2
    assert len(parsed.checksum) == 64
    assert parsed.entries == (
        PackIndexEntry(first, 0x12345678, 12),
        PackIndexEntry(second, 0x90ABCDEF, 44),
    )
    assert parsed.fanout[0] == 1
    assert parsed.fanout[-1] == 2


def test_parser_accepts_empty_index() -> None:
    parsed = parse_index_bytes(_index_bytes([]))
    assert parsed.object_count == 0
    assert parsed.entries == ()
    assert parsed.fanout == (0,) * 256


def test_checksum_and_exact_length_corruption_are_rejected() -> None:
    oid = "11" + "2" * 62
    valid = _index_bytes([(oid, 1, 12)])

    damaged = bytearray(valid)
    damaged[-1] ^= 0x01
    with pytest.raises(ValueError, match="checksum"):
        parse_index_bytes(bytes(damaged))

    with pytest.raises(ValueError, match="size mismatch"):
        parse_index_bytes(valid[:-1])
    with pytest.raises(ValueError, match="size mismatch"):
        parse_index_bytes(valid[:-32] + b"x" + valid[-32:])


def test_fanout_and_oid_table_corruption_are_rejected() -> None:
    first = "10" + "1" * 62
    second = "20" + "2" * 62
    valid = _index_bytes([(first, 1, 12), (second, 2, 40)])

    fanout = bytearray(valid)
    # Bucket 0x10 should cumulatively contain one object.  Setting it to zero
    # remains monotonic but no longer describes the OID table.
    fanout[8 + 0x10 * 4 : 12 + 0x10 * 4] = struct.pack(">I", 0)
    with pytest.raises(ValueError, match="fan-out table does not match"):
        parse_index_bytes(_rechecksum(fanout))

    bad_hex = bytearray(valid)
    bad_hex[1032] = ord("G")
    with pytest.raises(ValueError, match="canonical 64-hex"):
        parse_index_bytes(_rechecksum(bad_hex))

    unsorted = bytearray(valid)
    a = bytes(unsorted[1032:1096])
    b = bytes(unsorted[1096:1160])
    unsorted[1032:1096], unsorted[1096:1160] = b, a
    with pytest.raises(ValueError, match="strictly increasing"):
        parse_index_bytes(_rechecksum(unsorted))


def test_invalid_offsets_are_rejected() -> None:
    first = "01" + "a" * 62
    second = "02" + "b" * 62
    with pytest.raises(ValueError, match="before the pack header"):
        parse_index_bytes(_index_bytes([(first, 1, 0)]))
    with pytest.raises(ValueError, match="duplicate object offsets"):
        parse_index_bytes(_index_bytes([(first, 1, 12), (second, 2, 12)]))


def test_packwriter_index_round_trips_and_packreader_uses_strict_parser(tmp_path: Path) -> None:
    _, idx_path = _sample_pack(tmp_path)
    parsed = parse_index(idx_path)
    reader = PackReader(idx_path)

    assert reader.get_shas() == [entry.oid for entry in parsed.entries]
    assert all(reader.read_object(entry.oid).type_name == b"blob" for entry in parsed.entries)

    damaged = bytearray(idx_path.read_bytes())
    damaged[-1] ^= 0x01
    idx_path.write_bytes(damaged)
    with pytest.raises(ValueError, match="checksum"):
        PackReader(idx_path)


def test_show_index_file_stdin_verbose_and_count(tmp_path: Path, capsys) -> None:
    _, idx_path = _sample_pack(tmp_path)
    parsed = parse_index(idx_path)

    assert run_show_index([str(idx_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{entry.offset} {entry.oid}" for entry in parsed.entries
    ]

    assert run_show_index(["--verbose", str(idx_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{entry.offset} {entry.oid} {entry.crc32:08x}" for entry in parsed.entries
    ]

    result = subprocess.run(
        [sys.executable, "-m", "pygit", "show-index", "--count"],
        input=idx_path.read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"2\n"


def test_show_index_reports_corruption_through_installed_launcher(tmp_path: Path) -> None:
    _, idx_path = _sample_pack(tmp_path)
    damaged = bytearray(idx_path.read_bytes())
    damaged[-1] ^= 0x01

    result = subprocess.run(
        [sys.executable, "-m", "pygit", "show-index"],
        input=bytes(damaged),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 1
    assert b"checksum" in result.stderr.lower()
