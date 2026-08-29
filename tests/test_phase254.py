from __future__ import annotations

from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage
from pygit.rev_list_filter_omitted_cli import _partition_projected_lines


def _ordinary_three_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
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


def _count_output(output: str):
    lines = [line for line in output.splitlines() if line]
    assert lines
    count = int(lines[-1])
    omitted = [line[1:] for line in lines[:-1] if line.startswith("~")]
    other = [line for line in lines[:-1] if not line.startswith("~")]
    return count, omitted, other


def test_filter_print_omitted_blob_none_count_reports_omitted_before_count(
    tmp_path, monkeypatch, capsys
):
    repo = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--count",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    count, omitted, other = _count_output(capsys.readouterr().out)
    assert count == 6
    assert other == []
    assert len(omitted) == 3
    assert all(len(oid) == 64 for oid in omitted)
    assert all(repo.store.read(oid).type_name == b"blob" for oid in omitted)


def test_filter_print_omitted_object_type_count_respects_provided_exemption(
    tmp_path, monkeypatch, capsys
):
    repo = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--count",
            "--filter=object:type=tree",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    count, omitted, other = _count_output(capsys.readouterr().out)
    assert count == 4
    assert other == []
    omitted_types = [repo.store.read(oid).type_name for oid in omitted]
    assert omitted_types.count(b"commit") == 2
    assert omitted_types.count(b"blob") == 3


def test_filter_print_omitted_object_type_count_can_filter_provided_root(
    tmp_path, monkeypatch, capsys
):
    repo = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--count",
            "--filter=object:type=tree",
            "--filter-provided-objects",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    count, omitted, other = _count_output(capsys.readouterr().out)
    assert count == 3
    assert other == []
    omitted_types = [repo.store.read(oid).type_name for oid in omitted]
    assert omitted_types.count(b"commit") == 3
    assert omitted_types.count(b"blob") == 3


def test_omitted_partition_places_missing_before_final_count():
    traversal, missing, count = _partition_projected_lines(
        (
            "a" * 64,
            "?" + "b" * 40 + " path=f.txt type=blob",
            "7",
        ),
        count_mode=True,
    )
    assert traversal == ("a" * 64,)
    assert missing == ("?" + "b" * 40 + " path=f.txt type=blob",)
    assert count == "7"
