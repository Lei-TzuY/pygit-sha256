from __future__ import annotations

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


def _tokens(output: str) -> list[str]:
    return [line.split(None, 1)[0] for line in output.splitlines() if line]


def _omitted(tokens: list[str]) -> list[str]:
    return [token[1:] for token in tokens if token.startswith("~")]


def test_blob_none_boundary_includes_boundary_snapshot_blob_and_orders_after_traversal(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, _c3) = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    base_args = [
        "--objects",
        "--max-count=1",
        "--filter=blob:none",
        "--filter-print-omitted",
        "--missing=allow-promisor",
        "HEAD",
    ]
    assert run_rev_list_disk_usage(base_args) == 0
    without_boundary = _tokens(capsys.readouterr().out)
    assert len(_omitted(without_boundary)) == 1

    with_boundary_args = list(base_args)
    with_boundary_args.insert(1, "--boundary")
    assert run_rev_list_disk_usage(with_boundary_args) == 0
    tokens = _tokens(capsys.readouterr().out)

    boundary = f"-{c2}"
    omitted = _omitted(tokens)
    assert boundary in tokens
    assert len(omitted) == 2
    assert all(repo.store.read(oid).type_name == b"blob" for oid in omitted)
    first_omitted = min(i for i, token in enumerate(tokens) if token.startswith("~"))
    assert tokens.index(boundary) < first_omitted
    assert f"~{c2}" not in tokens


def test_object_type_tree_boundary_moves_filtered_boundary_commit_to_omitted(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3) = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
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
    omitted = _omitted(tokens)
    assert c3 in tokens
    assert f"-{c2}" not in tokens
    assert c2 in omitted
    assert sum(repo.store.read(oid).type_name == b"commit" for oid in omitted) == 1
    assert sum(repo.store.read(oid).type_name == b"blob" for oid in omitted) == 2
    kept_local = [token for token in tokens if not token.startswith(("~", "-", "?")) and token != c3]
    assert kept_local
    assert all(repo.store.read(token).type_name == b"tree" for token in kept_local)
    first_omitted = min(i for i, token in enumerate(tokens) if token.startswith("~"))
    assert all(not token.startswith("~") for token in tokens[:first_omitted])


def test_filter_provided_objects_boundary_omits_positive_root_and_boundary_commit(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3) = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
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
    omitted = _omitted(tokens)
    assert c3 not in tokens
    assert f"-{c2}" not in tokens
    assert c3 in omitted
    assert c2 in omitted
    assert sum(repo.store.read(oid).type_name == b"commit" for oid in omitted) == 2
    kept_local = [token for token in tokens if not token.startswith(("~", "-", "?"))]
    assert kept_local
    assert all(repo.store.read(token).type_name == b"tree" for token in kept_local)


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

    omitted = _omitted(_tokens(capsys.readouterr().out))
    assert omitted
    assert all(len(oid) == 64 for oid in omitted)
    assert all(all(ch in "0123456789abcdef" for ch in oid) for oid in omitted)
    assert all(repo.store.read(oid).type_name == b"blob" for oid in omitted)
