from __future__ import annotations

import shutil
import subprocess

import pytest

from pygit.objects import Identity, TagObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    (repo.worktree / "f.txt").write_text("payload\n", encoding="utf-8")
    repo.add(["f.txt"])
    commit = repo.commit(
        "c1",
        author_name="Test",
        author_email="test@example.com",
        commit_date="1",
    )
    tag = repo.store.write(
        TagObject(
            target_sha=commit,
            target_type=b"commit",
            tag_name="v1",
            tagger=Identity("Tagger", "tagger@example.com", 2, "+0000"),
            message="release",
        )
    )
    repo.refs.set_branch("main", commit, message="test: tag fixture")
    repo.refs.set_head_symbolic("main", message="test: tag fixture")
    tag_ref = repo.pygit_dir / "refs" / "tags" / "v1"
    tag_ref.parent.mkdir(parents=True, exist_ok=True)
    tag_ref.write_text(tag + "\n", encoding="ascii")
    return repo, commit, tag


def _patch_repo(monkeypatch, repo):
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)


def test_ordered_object_type_tag_keeps_provided_commit_then_tag(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tag = _repo(tmp_path)
    _patch_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tag",
            "--no-object-names",
            "v1",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [commit, tag]


def test_ordered_object_type_tag_filter_provided_keeps_only_tag(
    tmp_path, monkeypatch, capsys
):
    repo, _commit, tag = _repo(tmp_path)
    _patch_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tag",
            "--filter-provided-objects",
            "--no-object-names",
            "v1",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [tag]


def test_ordered_object_type_tag_count_matches_native_provided_semantics(
    tmp_path, monkeypatch, capsys
):
    repo, _commit, _tag = _repo(tmp_path)
    _patch_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "--filter=object:type=tag", "--count", "v1"]
    ) == 0
    assert capsys.readouterr().out == "2\n"

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tag",
            "--filter-provided-objects",
            "--count",
            "v1",
        ]
    ) == 0
    assert capsys.readouterr().out == "1\n"


def test_ordered_object_type_tag_nul_uses_local_sha256_records(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tag = _repo(tmp_path)
    _patch_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--filter=object:type=tag",
            "--no-object-names",
            "v1",
        ]
    ) == 0
    assert capsys.readouterr().out == f"{commit}\0{tag}\0"
    assert len(commit) == 64 and len(tag) == 64


def test_ordered_object_type_tag_has_empty_omitted_channel(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tag = _repo(tmp_path)
    _patch_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tag",
            "--filter-print-omitted",
            "--no-object-names",
            "v1",
        ]
    ) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [commit, tag]
    assert "~" not in out


def test_ordered_object_type_tag_keeps_staged_non_tag_root_guard(
    tmp_path, monkeypatch
):
    repo, _commit, _tag = _repo(tmp_path)
    _patch_repo(monkeypatch, repo)

    with pytest.raises(ValueError, match="object:type=tag"):
        run_rev_list_disk_usage(
            ["--objects", "--in-commit-order", "--filter=object:type=tag", "HEAD"]
        )


def test_native_sha256_git_matches_annotated_tag_order_and_filter_provided(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")

    native = tmp_path / "native"
    probe = subprocess.run(
        [git, "init", "--object-format=sha256", str(native)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("native git lacks SHA-256 repository support")

    subprocess.run([git, "config", "user.name", "Test"], cwd=native, check=True)
    subprocess.run([git, "config", "user.email", "test@example.com"], cwd=native, check=True)
    (native / "f.txt").write_text("payload\n", encoding="utf-8")
    subprocess.run([git, "add", "f.txt"], cwd=native, check=True)
    subprocess.run([git, "commit", "-m", "c1"], cwd=native, stdout=subprocess.PIPE, check=True)
    subprocess.run([git, "tag", "-a", "v1", "-m", "release"], cwd=native, check=True)

    commit = subprocess.check_output([git, "rev-parse", "v1^{}"], cwd=native, text=True).strip()
    tag = subprocess.check_output([git, "rev-parse", "v1"], cwd=native, text=True).strip()

    ordinary = subprocess.check_output(
        [
            git,
            "rev-list",
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tag",
            "--no-object-names",
            "v1",
        ],
        cwd=native,
        text=True,
    ).splitlines()
    filtered = subprocess.check_output(
        [
            git,
            "rev-list",
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tag",
            "--filter-provided-objects",
            "--no-object-names",
            "v1",
        ],
        cwd=native,
        text=True,
    ).splitlines()

    assert ordinary == [commit, tag]
    assert filtered == [tag]
    assert len(commit) == 64 and len(tag) == 64