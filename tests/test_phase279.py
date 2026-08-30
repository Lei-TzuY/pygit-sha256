from __future__ import annotations

import hashlib

import pytest

from pygit.cat_file import object_disk_size
from pygit.count_objects_cli import _human_size
from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import Identity, TagObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _write_tag(repo: Repository, *, name: str, target: str, target_type: bytes) -> str:
    oid = repo.store.write(
        TagObject(
            target_sha=target,
            target_type=target_type,
            tag_name=name,
            tagger=Identity("Tagger", "tagger@example.com", 20, "+0000"),
            message=f"{name} annotation",
        )
    )
    path = repo.pygit_dir / "refs" / "tags" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(oid + "\n", encoding="ascii")
    return oid


def _ordinary_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    commits = []
    for index, payload in enumerate(("one\n", "two\n"), start=1):
        (repo.worktree / "f.txt").write_text(payload, encoding="utf-8")
        repo.add(["f.txt"])
        commits.append(
            repo.commit(
                f"c{index}",
                author_name="Test",
                author_email="test@example.com",
                commit_date=str(index),
            )
        )
    tag1 = _write_tag(repo, name="v1", target=commits[-1], target_type=b"commit")
    tag2 = _write_tag(repo, name="v2", target=tag1, target_type=b"tag")
    return repo, tuple(commits), tag1, tag2


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _partial_tag_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "partial"))
    repo.add_remote("origin", "https://example.test/repo.git")

    native_blob = _native_oid("blob", b"payload\n")
    tree_data = b"100644 f.txt\0" + bytes.fromhex(native_blob)
    native_tree = _native_oid("tree", tree_data)
    commit_data = (
        f"tree {native_tree}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\nmsg\n"
    ).encode()
    native_commit = _native_oid("commit", commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            native_tree: NativeObject("tree", tree_data, native_tree),
            native_commit: NativeObject("commit", commit_data, native_commit),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_commit = importer.import_oid(native_commit)
    repo.refs.set_branch("main", local_commit, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    tag = _write_tag(repo, name="v1", target=local_commit, target_type=b"commit")
    return repo, local_commit, tag, native_blob


def _run(repo, monkeypatch, capsys, *args):
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()
    assert run_rev_list_disk_usage(list(args)) == 0
    return capsys.readouterr().out


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("annotated-tag disk usage must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("annotated-tag disk usage must not batch-fetch"),
    )


def test_tag_disk_usage_counts_provided_commit_and_nested_tags(tmp_path, monkeypatch, capsys):
    repo, (_c1, c2), tag1, tag2 = _ordinary_repo(tmp_path)
    expected = sum(object_disk_size(repo, oid) for oid in (c2, tag2, tag1))

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--disk-usage",
        "v2",
    )

    assert out == f"{expected}\n"


def test_tag_disk_usage_filter_provided_sizes_only_matching_tags(tmp_path, monkeypatch, capsys):
    repo, (_c1, _c2), tag1, tag2 = _ordinary_repo(tmp_path)
    expected = object_disk_size(repo, tag2) + object_disk_size(repo, tag1)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "--disk-usage",
        "v2",
    )

    assert out == f"{expected}\n"


@pytest.mark.parametrize("requested", ["commit", "tree", "blob"])
def test_existing_object_type_disk_usage_includes_provided_tag(
    tmp_path, monkeypatch, capsys, requested
):
    repo, _commits, _tag1, _tag2 = _ordinary_repo(tmp_path)

    selection = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--no-object-names",
        f"--filter=object:type={requested}",
        "--missing=allow-promisor",
        "v1",
    ).splitlines()
    expected = sum(
        object_disk_size(repo, line.lstrip("-"))
        for line in selection
        if len(line.lstrip("-")) == 64
    )

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        f"--filter=object:type={requested}",
        "--disk-usage",
        "v1",
    )
    assert out == f"{expected}\n"


def test_tag_disk_usage_count_and_human_framing(tmp_path, monkeypatch, capsys):
    repo, (_c1, c2), tag1, _tag2 = _ordinary_repo(tmp_path)
    expected = object_disk_size(repo, c2) + object_disk_size(repo, tag1)

    assert _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--count",
        "--filter=object:type=tag",
        "--disk-usage",
        "v1",
    ) == f"0\n{expected}\n"

    assert _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--disk-usage=human",
        "v1",
    ) == f"{_human_size(expected)}\n"


def test_tag_disk_usage_boundary_sizes_boundary_commit_but_not_twice(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2), tag1, _tag2 = _ordinary_repo(tmp_path)
    expected = sum(object_disk_size(repo, oid) for oid in (c2, c1, tag1))

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--boundary",
        "--max-count=1",
        "--filter=object:type=commit",
        "--disk-usage",
        "v1",
    )
    assert out == f"{expected}\n"


def test_tag_disk_usage_object_edge_is_visible_but_not_sized(tmp_path, monkeypatch, capsys):
    repo, (c1, c2), tag1, _tag2 = _ordinary_repo(tmp_path)
    expected = object_disk_size(repo, c2) + object_disk_size(repo, tag1)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects-edge",
        "--filter=object:type=tag",
        "--disk-usage",
        f"{c1}..v1",
    )
    assert out.splitlines() == [f"-{c1}", str(expected)]


def test_tag_disk_usage_partial_clone_filters_missing_blob_without_fetch(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tag, _native_blob = _partial_tag_repo(tmp_path)
    before = read_promisor_state(repo.pygit_dir)
    _disable_fetch(monkeypatch)
    expected = object_disk_size(repo, commit) + object_disk_size(repo, tag)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--disk-usage",
        "v1",
    )

    assert out == f"{expected}\n"
    assert read_promisor_state(repo.pygit_dir) == before


def test_tag_disk_usage_partial_matching_missing_record_is_not_sized(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tag, native_blob = _partial_tag_repo(tmp_path)
    before = read_promisor_state(repo.pygit_dir)
    _disable_fetch(monkeypatch)
    expected = object_disk_size(repo, commit) + object_disk_size(repo, tag)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=blob",
        "--missing=print-info",
        "--disk-usage",
        "v1",
    )

    lines = out.splitlines()
    assert lines[-1] == str(expected)
    assert lines[0].startswith(f"?{native_blob}")
    assert "type=blob" in lines[0]
    assert read_promisor_state(repo.pygit_dir) == before


def test_tag_disk_usage_ordinary_missing_fails_before_output(tmp_path, monkeypatch, capsys):
    repo, _commit, _tag, native_blob = _partial_tag_repo(tmp_path)
    _disable_fetch(monkeypatch)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    with pytest.raises(RuntimeError, match=native_blob):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--filter=object:type=blob",
                "--disk-usage",
                "v1",
            ]
        )
    assert capsys.readouterr().out == ""


def test_tag_disk_usage_retains_git_255_nul_rejection(tmp_path, monkeypatch):
    repo, _commits, _tag1, _tag2 = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="Git 2.55"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "-z",
                "--filter=object:type=tag",
                "--disk-usage",
                "v1",
            ]
        )
