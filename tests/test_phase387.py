from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from pygit.git_bundle import parse_git_bundle


def _git(repo: Path, *args: str, text: bool = True):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=text,
    )


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True)


def _make_sha1_repo(tmp_path: Path):
    repo = tmp_path / "source"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _run(repo, "config", "user.name", "Bundle Tester")
    _run(repo, "config", "user.email", "bundle@example.invalid")
    (repo / "payload.txt").write_text("one\n", encoding="utf-8")
    _run(repo, "add", "payload.txt")
    _run(repo, "commit", "-qm", "one")
    first = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "payload.txt").write_text("two\n", encoding="utf-8")
    _run(repo, "commit", "-qam", "two")
    second = _git(repo, "rev-parse", "HEAD").strip()
    return repo, first, second


def _create_bundle(repo: Path, path: Path, version: int, *revs: str) -> bytes:
    _run(repo, "bundle", "create", f"--version={version}", str(path), *revs)
    return path.read_bytes()


def _header_and_pack(data: bytes):
    boundary = data.index(b"\n\n") + 2
    return data[:boundary], data[boundary:]


def _replace_first_ref_oid(data: bytes, oid: bytes) -> bytes:
    lines = data.split(b"\n")
    for index, line in enumerate(lines):
        if line and not line.startswith((b"#", b"@", b"-")):
            _old, space, refname = line.partition(b" ")
            assert space
            lines[index] = oid + b" " + refname
            return b"\n".join(lines)
    raise AssertionError("bundle did not contain a reference")


def test_native_v2_bundle_is_verified_and_expanded(tmp_path):
    repo, _first, head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "full-v2.bundle", 2, "main")

    parsed = parse_git_bundle(data)

    assert parsed.version == 2
    assert parsed.object_format == "sha1"
    assert parsed.capabilities == {}
    assert parsed.prerequisites == ()
    assert parsed.refs == {"refs/heads/main": head}
    assert parsed.filter_spec is None
    assert parsed.is_self_contained
    assert not parsed.is_filtered
    assert not parsed.requires_prerequisites
    assert parsed.pack.startswith(b"PACK")
    assert parsed.pack_version in (2, 3)
    assert parsed.pack_entries >= 3
    assert parsed.objects is not None
    assert head in parsed.objects
    assert parsed.objects[head].type_name == "commit"
    assert {obj.type_name for obj in parsed.objects.values()} >= {"commit", "tree", "blob"}


def test_native_v3_sha1_bundle_preserves_object_format(tmp_path):
    repo, _first, head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "full-v3.bundle", 3, "main")

    parsed = parse_git_bundle(data)

    assert parsed.version == 3
    assert parsed.object_format == "sha1"
    assert parsed.capabilities.get("object-format") == b"sha1"
    assert parsed.refs["refs/heads/main"] == head
    assert parsed.objects is not None
    assert head in parsed.objects


def test_v3_without_object_format_defaults_to_sha1_like_native_git(tmp_path):
    repo, _first, head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "v3.bundle", 3, "main")
    data = data.replace(b"@object-format=sha1\n", b"", 1)

    parsed = parse_git_bundle(data)

    assert parsed.object_format == "sha1"
    assert parsed.refs["refs/heads/main"] == head


def test_native_incremental_bundle_preserves_prerequisite_without_fake_expansion(tmp_path):
    repo, first, head = _make_sha1_repo(tmp_path)
    data = _create_bundle(
        repo,
        tmp_path / "incremental.bundle",
        2,
        f"{first}..main",
    )

    parsed = parse_git_bundle(data)

    assert parsed.refs == {"refs/heads/main": head}
    assert len(parsed.prerequisites) == 1
    assert parsed.prerequisites[0].oid == first
    assert parsed.prerequisites[0].comment == b"one"
    assert parsed.requires_prerequisites
    assert not parsed.is_self_contained
    assert parsed.objects is None
    assert parsed.pack.startswith(b"PACK")
    assert parsed.pack_entries > 0


def test_filter_capability_marks_graph_incomplete_even_without_prerequisites(tmp_path):
    repo, _first, head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "filtered-marker.bundle", 3, "main")
    data = data.replace(
        b"# v3 git bundle\n",
        b"# v3 git bundle\n@filter=blob:none\n",
        1,
    )

    parsed = parse_git_bundle(data)

    assert parsed.refs["refs/heads/main"] == head
    assert parsed.filter_spec == "blob:none"
    assert parsed.is_filtered
    assert not parsed.is_self_contained
    # Contained objects are still safe to parse; the filter flag prevents callers
    # from confusing that object set with a complete reachable graph.
    assert parsed.objects is not None
    assert head in parsed.objects


def test_unknown_v3_capability_fails_closed(tmp_path):
    repo, _first, _head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "unknown.bundle", 3, "main")
    data = data.replace(
        b"# v3 git bundle\n",
        b"# v3 git bundle\n@future-required=1\n",
        1,
    )

    with pytest.raises(RuntimeError, match="Unsupported Git bundle capability"):
        parse_git_bundle(data)


def test_duplicate_capability_is_rejected(tmp_path):
    repo, _first, _head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "duplicate-cap.bundle", 3, "main")
    data = data.replace(
        b"@object-format=sha1\n",
        b"@object-format=sha1\n@object-format=sha1\n",
        1,
    )

    with pytest.raises(ValueError, match="Duplicate Git bundle capability"):
        parse_git_bundle(data)


def test_native_sha256_v3_bundle_is_rejected_at_remote_identity_boundary(tmp_path):
    repo = tmp_path / "sha256-source"
    subprocess.run(
        ["git", "init", "-q", "--object-format=sha256", "-b", "main", str(repo)],
        check=True,
    )
    _run(repo, "config", "user.name", "Bundle Tester")
    _run(repo, "config", "user.email", "bundle@example.invalid")
    (repo / "file.txt").write_text("sha256\n", encoding="utf-8")
    _run(repo, "add", "file.txt")
    _run(repo, "commit", "-qm", "sha256")
    data = _create_bundle(repo, tmp_path / "sha256.bundle", 3, "main")

    assert b"@object-format=sha256\n" in data
    # The 64-hex advertised tip crosses the unsupported remote hash domain
    # before pack parsing, so the earliest fail-closed diagnostic is the native
    # OID-width check rather than the later capability check.
    with pytest.raises(ValueError, match="40-hex SHA-1"):
        parse_git_bundle(data)


def test_corrupt_pack_checksum_is_rejected(tmp_path):
    repo, _first, _head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "checksum.bundle", 2, "main")
    header, pack = _header_and_pack(data)
    corrupted = bytearray(pack)
    corrupted[-21] ^= 0x01

    with pytest.raises(ValueError, match="checksum mismatch"):
        parse_git_bundle(header + bytes(corrupted))


def test_structural_trailing_byte_is_rejected_even_with_recomputed_checksum(tmp_path):
    repo, _first, _head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "trailing.bundle", 2, "main")
    header, pack = _header_and_pack(data)
    body = pack[:-20] + b"X"
    malformed_pack = body + hashlib.sha1(body).digest()

    with pytest.raises(ValueError, match="Trailing bytes"):
        parse_git_bundle(header + malformed_pack)


def test_self_contained_reference_must_name_an_object_in_the_pack(tmp_path):
    repo, _first, _head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "missing-tip.bundle", 2, "main")
    data = _replace_first_ref_oid(data, b"f" * 40)

    with pytest.raises(ValueError, match="references objects absent"):
        parse_git_bundle(data)


def test_64_hex_local_sha256_identity_is_not_accepted_as_bundle_ref(tmp_path):
    repo, _first, _head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "wrong-domain.bundle", 2, "main")
    data = _replace_first_ref_oid(data, b"a" * 64)

    with pytest.raises(ValueError, match="40-hex SHA-1"):
        parse_git_bundle(data)


def test_prerequisite_after_reference_is_rejected(tmp_path):
    repo, first, _head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "order.bundle", 2, "main")
    header, pack = _header_and_pack(data)
    lines = header[:-2].split(b"\n")
    lines.append(b"-" + first.encode("ascii") + b" late")
    malformed = b"\n".join(lines) + b"\n\n" + pack

    with pytest.raises(ValueError, match="prerequisite appears after"):
        parse_git_bundle(malformed)


def test_invalid_bundle_refname_is_rejected(tmp_path):
    repo, _first, _head = _make_sha1_repo(tmp_path)
    data = _create_bundle(repo, tmp_path / "refname.bundle", 2, "main")
    lines = data.split(b"\n")
    for index, line in enumerate(lines):
        if line and not line.startswith((b"#", b"@", b"-")):
            oid, _space, _name = line.partition(b" ")
            lines[index] = oid + b" refs/heads/bad..name"
            break
    malformed = b"\n".join(lines)

    with pytest.raises(ValueError, match="Invalid bundle reference name"):
        parse_git_bundle(malformed)


def test_native_git_verify_accepts_the_same_full_v2_and_v3_inputs(tmp_path):
    repo, _first, _head = _make_sha1_repo(tmp_path)
    for version in (2, 3):
        path = tmp_path / f"native-v{version}.bundle"
        data = _create_bundle(repo, path, version, "main")
        subprocess.run(
            ["git", "-C", str(repo), "bundle", "verify", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        parsed = parse_git_bundle(data)
        assert parsed.version == version
        assert parsed.refs
        assert parsed.objects
