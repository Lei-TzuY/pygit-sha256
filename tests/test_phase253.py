from __future__ import annotations

import pytest

from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


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


def _split_output(repo: Repository, output: str):
    kept = []
    omitted = []
    for line in output.splitlines():
        if not line:
            continue
        token = line.split(None, 1)[0]
        if token.startswith("~"):
            oid = token[1:].lower()
            assert len(oid) == 64
            assert all(ch in "0123456789abcdef" for ch in oid)
            omitted.append(oid)
        elif token.startswith("?"):
            kept.append(("missing", token[1:].lower()))
        else:
            kept.append((repo.store.read(token.lower()).type_name.decode("ascii"), token.lower()))
    return kept, omitted


def test_filter_print_omitted_reports_local_blob_none_objects(
    tmp_path, monkeypatch, capsys
):
    repo = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    kept, omitted = _split_output(repo, capsys.readouterr().out)
    assert omitted
    assert all(repo.store.read(oid).type_name == b"blob" for oid in omitted)
    assert all(kind != "blob" for kind, _oid in kept)
    assert len(omitted) == 3


def test_filter_print_omitted_object_type_respects_filter_provided_objects(
    tmp_path, monkeypatch, capsys
):
    repo = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=object:type=tree",
            "--filter-provided-objects",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    kept, omitted = _split_output(repo, capsys.readouterr().out)
    assert kept
    assert all(kind == "tree" for kind, _oid in kept)
    omitted_types = {repo.store.read(oid).type_name for oid in omitted}
    assert omitted_types == {b"commit", b"blob"}


def test_filter_print_omitted_requires_filter(tmp_path, monkeypatch):
    repo = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="requires --filter"):
        run_rev_list_disk_usage(
            ["--objects", "--filter-print-omitted", "--missing=allow-promisor", "HEAD"]
        )


@pytest.mark.parametrize("option", ["-z", "--objects-edge"])
def test_filter_print_omitted_defers_unmodelled_framing(
    tmp_path, monkeypatch, option
):
    repo = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    args = [
        "--objects",
        "--filter=blob:none",
        "--filter-print-omitted",
        "--missing=allow-promisor",
        "HEAD",
    ]
    if option == "--objects-edge":
        args[0] = "--objects-edge"
    else:
        args.insert(1, option)

    with pytest.raises(ValueError, match="not yet supported"):
        run_rev_list_disk_usage(args)
