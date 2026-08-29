from __future__ import annotations

from pygit.objects import CommitObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _changing_three_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    commits = []
    for index in range(1, 4):
        (repo.worktree / "f.txt").write_text(f"{index}\n", encoding="utf-8")
        repo.add(["f.txt"])
        commits.append(
            repo.commit(
                f"c{index}",
                author_name="Test",
                author_email="test@example.com",
                commit_date=str(index),
            )
        )
    return repo, tuple(commits)


def _tree_and_blob(repo: Repository, commit_oid: str) -> tuple[str, str]:
    commit = repo.store.read(commit_oid)
    assert isinstance(commit, CommitObject)
    tree_oid = commit.tree.lower()
    tree = repo.store.read(tree_oid)
    entry = tree.entries[0]
    return tree_oid, entry.oid.lower()


def _tokens(output: str) -> list[str]:
    return [line.split(None, 1)[0] for line in output.splitlines() if line]


def test_blob_none_boundary_prints_boundary_before_omitted_snapshot_blobs(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3) = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree2, blob2 = _tree_and_blob(repo, c2)
    _tree3, blob3 = _tree_and_blob(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    tokens = _tokens(capsys.readouterr().out)
    boundary = f"-{c2}"
    assert boundary in tokens
    assert f"~{blob2}" in tokens
    assert f"~{blob3}" in tokens
    assert tokens.index(boundary) < tokens.index(f"~{blob2}")
    assert tokens.index(boundary) < tokens.index(f"~{blob3}")
    assert all(token != f"~{c2}" for token in tokens)


def test_object_type_tree_boundary_moves_filtered_boundary_commit_to_omitted(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3) = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, blob2 = _tree_and_blob(repo, c2)
    tree3, blob3 = _tree_and_blob(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "--filter=object:type=tree",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    tokens = _tokens(capsys.readouterr().out)
    assert c3 in tokens
    assert tree2 in tokens
    assert tree3 in tokens
    assert f"-{c2}" not in tokens
    assert f"~{c2}" in tokens
    assert f"~{blob2}" in tokens
    assert f"~{blob3}" in tokens
    first_omitted = min(i for i, token in enumerate(tokens) if token.startswith("~"))
    assert all(not token.startswith("~") for token in tokens[:first_omitted])


def test_filter_provided_objects_boundary_omits_positive_root_and_boundary_commit(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3) = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, _blob2 = _tree_and_blob(repo, c2)
    tree3, _blob3 = _tree_and_blob(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "--filter=object:type=tree",
            "--filter-provided-objects",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    tokens = _tokens(capsys.readouterr().out)
    assert c3 not in tokens
    assert f"~{c3}" in tokens
    assert f"~{c2}" in tokens
    assert f"-{c2}" not in tokens
    assert tree2 in tokens
    assert tree3 in tokens


def test_boundary_omitted_records_are_genuine_local_sha256(
    tmp_path, monkeypatch, capsys
):
    repo, _commits = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    omitted = [token[1:] for token in _tokens(capsys.readouterr().out) if token.startswith("~")]
    assert omitted
    assert all(len(oid) == 64 for oid in omitted)
    assert all(all(ch in "0123456789abcdef" for ch in oid) for oid in omitted)
    assert all(repo.store.read(oid).type_name == b"blob" for oid in omitted)
