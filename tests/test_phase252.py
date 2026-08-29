from __future__ import annotations

import pytest

from pygit.objects import CommitObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _ordinary_three_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    commits = []
    for index, name in enumerate(("a.txt", "b.txt", "c.txt"), start=1):
        (repo.worktree / name).write_text(f"{index}\n", encoding="utf-8")
        repo.add([name])
        commits.append(
            repo.commit(
                f"c{index}",
                author_name="Test",
                author_email="test@example.com",
                commit_date=str(index),
            )
        )
    return repo, tuple(commits)


def _tree(repo: Repository, commit_oid: str) -> str:
    obj = repo.store.read(commit_oid)
    assert isinstance(obj, CommitObject)
    return obj.tree.lower()


def _records(raw: str):
    records = []
    current = None
    for token in raw.split("\0"):
        if not token:
            continue
        if "=" not in token:
            if current is not None:
                records.append(current)
            current = [token]
        else:
            assert current is not None
            current.append(token)
    if current is not None:
        records.append(current)
    return records


def test_filter_provided_objects_removes_head_exemption_in_nul_tree_filter(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=object:type=tree",
            "--filter-provided-objects",
            "HEAD",
        ]
    ) == 0

    records = _records(capsys.readouterr().out)
    oids = [record[0] for record in records]
    assert c1 not in oids
    assert c2 not in oids
    assert c3 not in oids
    assert oids == [_tree(repo, c3), _tree(repo, c2), _tree(repo, c1)]
    assert all(repo.store.read(oid).type_name == b"tree" for oid in oids)


def test_filter_provided_objects_changes_object_type_count(tmp_path, monkeypatch, capsys):
    repo, _commits = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=object:type=tree",
            "--filter-provided-objects",
            "--missing=allow-promisor",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.strip() == "3"


def test_filter_provided_objects_filters_all_ref_tips(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    repo.refs.set_branch("side", c2, message="test: side")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--all",
            "--filter=object:type=tree",
            "--filter-provided-objects",
        ]
    ) == 0

    records = _records(capsys.readouterr().out)
    oids = [record[0] for record in records]
    assert c1 not in oids
    assert c2 not in oids
    assert c3 not in oids
    assert set(oids) == {_tree(repo, c1), _tree(repo, c2), _tree(repo, c3)}


def test_filter_provided_objects_keeps_matching_commit_roots(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=object:type=commit",
            "--filter-provided-objects",
            "HEAD",
        ]
    ) == 0

    assert [record[0] for record in _records(capsys.readouterr().out)] == [c3, c2, c1]


def test_filter_provided_objects_is_accepted_with_blob_none(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:none",
            "--filter-provided-objects",
            "HEAD",
        ]
    ) == 0

    records = _records(capsys.readouterr().out)
    oids = [record[0] for record in records]
    assert c3 in oids
    assert c2 in oids
    assert c1 in oids
    assert all(repo.store.read(oid).type_name != b"blob" for oid in oids)


def test_filter_provided_objects_requires_filter(tmp_path, monkeypatch):
    repo, _commits = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="requires --filter"):
        run_rev_list_disk_usage(["--objects", "--filter-provided-objects", "HEAD"])
