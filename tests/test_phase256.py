from __future__ import annotations

from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _changing_three_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    commits = []
    blobs = []
    for index in range(1, 4):
        (repo.worktree / "f.txt").write_text(f"{index}\n", encoding="utf-8")
        repo.add(["f.txt"])
        commit = repo.commit(
            f"c{index}",
            author_name="Test",
            author_email="test@example.com",
            commit_date=str(index),
        )
        commits.append(commit)
        tree = repo.store.read(repo.store.read(commit).tree)
        blobs.append(tree.entries[0].oid)
    return repo, tuple(commits), tuple(blobs)


def _tokens(output: str) -> list[str]:
    return [line.split(None, 1)[0] for line in output.splitlines() if line]


def test_blob_none_objects_edge_keeps_edge_before_selected_omissions(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, _c2, _c3), (b1, b2, b3) = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            f"{c1}..HEAD",
        ]
    ) == 0

    tokens = _tokens(capsys.readouterr().out)
    edge = f"-{c1}"
    omitted = [token[1:] for token in tokens if token.startswith("~")]

    assert edge in tokens
    assert omitted == [b3, b2] or omitted == [b2, b3]
    assert b1 not in omitted
    assert all(len(oid) == 64 for oid in omitted)
    assert all(repo.store.read(oid).type_name == b"blob" for oid in omitted)
    assert tokens.index(edge) < min(i for i, token in enumerate(tokens) if token.startswith("~"))


def test_blob_none_objects_edge_count_keeps_edge_and_omissions_out_of_count(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, _c2, _c3), (_b1, b2, b3) = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--count",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            f"{c1}..HEAD",
        ]
    ) == 0

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    tokens = [line.split(None, 1)[0] for line in lines]
    omitted = [token[1:] for token in tokens if token.startswith("~")]

    assert f"-{c1}" in tokens
    assert set(omitted) == {b2, b3}
    assert lines[-1] == "4"
    first_omitted = min(i for i, token in enumerate(tokens) if token.startswith("~"))
    assert tokens.index(f"-{c1}") < first_omitted < len(tokens) - 1


def test_object_type_objects_edge_preserves_edge_and_native_empty_omit_set(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, _c2, _c3), _blobs = _changing_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--filter=object:type=tree",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            f"{c1}..HEAD",
        ]
    ) == 0

    tokens = _tokens(capsys.readouterr().out)
    assert f"-{c1}" in tokens
    assert not any(token.startswith("~") for token in tokens)
    kept = [token for token in tokens if not token.startswith(("-", "?", "~"))]
    assert kept
    assert all(repo.store.read(token).type_name == b"tree" for token in kept)
