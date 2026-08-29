from __future__ import annotations

from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage
from pygit.rev_list_filter_omitted_cli import _partition_projected_nul


def _repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    for index, name in enumerate(("a.txt", "b.txt", "c.txt"), start=1):
        (repo.worktree / name).write_text(f"{index}\n", encoding="utf-8")
        repo.add([name])
        repo.commit(
            f"c{index}",
            author_name="Test",
            author_email="test@example.com",
            commit_date=str(index),
        )
    return repo


def _assert_local_sha256(value: str) -> None:
    assert len(value) == 64
    assert all(ch in "0123456789abcdef" for ch in value.lower())


def test_blob_none_omitted_uses_native_mixed_nul_and_newline_framing(
    tmp_path, monkeypatch, capsys
):
    repo = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0
    output = capsys.readouterr().out

    # Current Git sets line_term/info_term to NUL for object records but prints
    # omitted_objects through a hard-coded "~%s\n" loop. Match that mixed wire
    # format exactly instead of inventing an omitted=yes metadata token.
    last_nul = output.rfind("\0")
    assert last_nul >= 0
    traversal = output[: last_nul + 1]
    omitted_suffix = output[last_nul + 1 :]

    traversal_oids = [
        field.lower()
        for field in traversal.split("\0")
        if len(field) == 64 and all(ch in "0123456789abcdef" for ch in field.lower())
    ]
    assert traversal_oids
    assert all(repo.store.read(oid).type_name != b"blob" for oid in traversal_oids)

    omitted = [line[1:].lower() for line in omitted_suffix.splitlines() if line]
    assert len(omitted) == 3
    for oid in omitted:
        _assert_local_sha256(oid)
        assert repo.store.read(oid).type_name == b"blob"
    assert all(line.startswith("~") for line in omitted_suffix.splitlines() if line)
    assert "omitted=yes" not in output


def test_object_type_nul_keeps_native_empty_omitted_set(
    tmp_path, monkeypatch, capsys
):
    repo = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=object:type=tree",
            "--filter-provided-objects",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0
    output = capsys.readouterr().out

    assert "\0" in output
    assert "~" not in output
    object_ids = [
        field.lower()
        for field in output.split("\0")
        if len(field) == 64 and all(ch in "0123456789abcdef" for ch in field.lower())
    ]
    assert object_ids
    assert all(repo.store.read(oid).type_name == b"tree" for oid in object_ids)


def test_nul_partition_keeps_missing_records_after_omitted_insertion_point():
    present = "a" * 64
    missing = "b" * 40
    projected = (
        f"{present}\0path=tree\0"
        f"{missing}\0missing=yes\0path=file.txt\0type=blob\0"
    )

    traversal, missing_records = _partition_projected_nul(projected)

    assert traversal == (f"{present}\0path=tree\0",)
    assert missing_records == (
        f"{missing}\0missing=yes\0path=file.txt\0type=blob\0",
    )
