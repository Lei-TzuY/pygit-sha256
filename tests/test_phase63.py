"""Phase 63 tests: strict annotated-tag object creation plumbing."""

from pathlib import Path

import pytest

from pygit import Repository, hash_object_data, make_tag, parse_tag_payload, validate_tag_payload
from pygit.launcher import _run_mktag
from pygit.objects import BlobObject, TagObject


def _payload(target: str, target_type: str = "blob", name: str = "v1.0") -> bytes:
    return (
        f"object {target}\n"
        f"type {target_type}\n"
        f"tag {name}\n"
        "tagger Tester <tester@example.com> 1 +0000\n"
        "\n"
        "release notes\n"
    ).encode("utf-8")


def test_make_tag_validates_and_writes_exact_payload(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    target = repo.store.write(BlobObject(b"payload"))
    payload = _payload(target)

    oid = make_tag(repo, payload)

    assert oid == hash_object_data(payload, "tag")
    obj = repo.store.read(oid)
    assert isinstance(obj, TagObject)
    assert obj.target_sha == target
    assert obj.target_type == b"blob"
    assert obj.tag_name == "v1.0"
    assert obj.message == "release notes\n"


def test_validate_tag_rejects_missing_target_and_type_mismatch(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))

    with pytest.raises(ValueError, match="does not exist"):
        validate_tag_payload(repo, _payload("0" * 64))

    target = repo.store.write(BlobObject(b"payload"))
    with pytest.raises(ValueError, match="type mismatch"):
        validate_tag_payload(repo, _payload(target, target_type="commit"))


def test_parse_tag_requires_canonical_headers_and_tag_name() -> None:
    oid = "1" * 64
    wrong_order = (
        f"type blob\nobject {oid}\ntag v1\n"
        "tagger Tester <tester@example.com> 1 +0000\n\nmessage"
    ).encode()
    with pytest.raises(ValueError, match="expected 'object'"):
        parse_tag_payload(wrong_order)

    extra_header = (
        f"object {oid}\ntype blob\ntag v1\n"
        "tagger Tester <tester@example.com> 1 +0000\nextra nope\n\nmessage"
    ).encode()
    with pytest.raises(ValueError, match="exactly four"):
        parse_tag_payload(extra_header)

    with pytest.raises(ValueError, match="reference name"):
        parse_tag_payload(_payload(oid, name="bad..name"))


def test_parse_tag_rejects_noncanonical_oid_and_tagger() -> None:
    upper = "A" * 64
    with pytest.raises(ValueError, match="lowercase"):
        parse_tag_payload(_payload(upper))

    oid = "a" * 64
    bad_tz = _payload(oid).replace(b"+0000", b"+2460")
    with pytest.raises(ValueError, match="timezone"):
        parse_tag_payload(bad_tz)

    noncanonical = _payload(oid).replace(
        b"Tester <tester@example.com> 1 +0000",
        b"Tester <tester@example.com> 01 +0000",
    )
    with pytest.raises(ValueError, match="canonical"):
        parse_tag_payload(noncanonical)


def test_mktag_can_target_an_existing_tag(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    blob = repo.store.write(BlobObject(b"payload"))
    first = make_tag(repo, _payload(blob, name="v1"))

    second_payload = _payload(first, target_type="tag", name="v1-alias")
    second = make_tag(repo, second_payload)

    obj = repo.store.read(second)
    assert isinstance(obj, TagObject)
    assert obj.target_sha == first
    assert obj.target_type == b"tag"


def test_mktag_cli_reads_stdin_and_prints_oid(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    target = repo.store.write(BlobObject(b"payload"))
    payload = _payload(target)
    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr("pygit.launcher._stdin_bytes", lambda: payload)
    capsys.readouterr()

    assert _run_mktag([]) == 0
    oid = capsys.readouterr().out.strip()
    assert oid == hash_object_data(payload, "tag")
    assert isinstance(repo.store.read(oid), TagObject)
