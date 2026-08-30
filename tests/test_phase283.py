from __future__ import annotations

import subprocess

from pygit.objects import CommitObject, TreeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _ordinary_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "small.bin").write_bytes(b"sss")
    (repo.worktree / "large.bin").write_bytes(b"LLLLLLLL")
    repo.add(["small.bin", "large.bin"])
    commit_oid = repo.commit(
        "payloads",
        author_name="Test",
        author_email="test@example.com",
        commit_date="1",
    )
    commit = repo.store.read(commit_oid)
    assert isinstance(commit, CommitObject)
    tree = repo.store.read(commit.tree)
    assert isinstance(tree, TreeObject)
    blobs = {entry.name: entry.sha.lower() for entry in tree.entries}
    return repo, commit_oid, commit.tree.lower(), blobs


def _route_repo(monkeypatch, repo):
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)


def test_blob_limit_omitted_nul_uses_mixed_git_framing(tmp_path, monkeypatch, capsys):
    repo, commit_oid, tree_oid, blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out == (
        f"{commit_oid}\0"
        f"{tree_oid}\0"
        f"{blobs['small.bin']}\0"
        f"~{blobs['large.bin']}\n"
    )


def test_blob_limit_omitted_nul_preserves_path_before_omission(tmp_path, monkeypatch, capsys):
    repo, _commit_oid, _tree_oid, blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    output = capsys.readouterr().out
    surviving = f"{blobs['small.bin']}\0path=small.bin\0"
    omitted = f"~{blobs['large.bin']}\n"
    assert surviving in output
    assert omitted in output
    assert output.index(surviving) < output.index(omitted)
    assert "path=large.bin" not in output


def test_blob_limit_omitted_nul_count_places_omission_before_count(tmp_path, monkeypatch, capsys):
    repo, _commit_oid, _tree_oid, blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out == f"~{blobs['large.bin']}\n3\n"


def test_native_git_sha256_blob_limit_omitted_nul_framing(tmp_path):
    repo = tmp_path / "native"
    subprocess.run(
        ["git", "init", "--object-format=sha256", str(repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (repo / "small.bin").write_bytes(b"sss")
    (repo / "large.bin").write_bytes(b"LLLLLLLL")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "payloads"],
        check=True,
        capture_output=True,
    )

    def rev_parse(expr: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", expr],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    commit_oid = rev_parse("HEAD")
    tree_oid = rev_parse("HEAD^{tree}")
    small_oid = rev_parse("HEAD:small.bin")
    large_oid = rev_parse("HEAD:large.bin")
    output = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-list",
            "--objects",
            "-z",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--no-object-names",
            "HEAD",
        ],
        check=True,
        capture_output=True,
    ).stdout

    assert output == (
        commit_oid.encode()
        + b"\0"
        + tree_oid.encode()
        + b"\0"
        + small_oid.encode()
        + b"\0~"
        + large_oid.encode()
        + b"\n"
    )
