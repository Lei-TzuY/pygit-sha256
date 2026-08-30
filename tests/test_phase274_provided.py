from __future__ import annotations

import pytest

from pygit.objects import CommitObject, Identity, TagObject, TreeObject
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
    tag_path = repo.pygit_dir / "refs" / "tags" / "v1"
    tag_path.parent.mkdir(parents=True, exist_ok=True)
    tag_path.write_text(tag + "\n", encoding="ascii")

    commit_obj = repo.store.read(commit)
    assert isinstance(commit_obj, CommitObject)
    tree = commit_obj.tree.lower()
    tree_obj = repo.store.read(tree)
    assert isinstance(tree_obj, TreeObject)
    blob = tree_obj.entries[0].sha.lower()
    return repo, commit, tag, tree, blob


def _run(repo, monkeypatch, capsys, *args):
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()
    assert run_rev_list_disk_usage(list(args)) == 0
    return capsys.readouterr().out


@pytest.mark.parametrize(
    ("requested", "tail"),
    [
        ("commit", "commit"),
        ("tree", "tree"),
        ("blob", "blob"),
    ],
)
def test_annotated_tag_is_provided_object_for_existing_object_type_filters(
    tmp_path, monkeypatch, capsys, requested, tail
):
    repo, commit, tag, tree, blob = _repo(tmp_path)
    expected_tail = {"commit": commit, "tree": tree, "blob": blob}[tail]

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        f"--filter=object:type={requested}",
        "--missing=print-info",
        "--no-object-names",
        "v1",
    )

    if requested == "commit":
        assert out.splitlines() == [commit, tag]
    else:
        assert out.splitlines() == [commit, tag, expected_tail]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [("commit", 1), ("tree", 1), ("blob", 1)],
)
def test_filter_provided_objects_filters_annotated_tag_for_non_tag_requests(
    tmp_path, monkeypatch, capsys, requested, expected
):
    repo, _commit, tag, _tree, _blob = _repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        f"--filter=object:type={requested}",
        "--filter-provided-objects",
        "--missing=print-info",
        "--no-object-names",
        "v1",
    )

    assert tag not in out
    assert len(out.splitlines()) == expected


@pytest.mark.parametrize(
    ("requested", "expected_default", "expected_filtered"),
    [
        ("commit", "2\n", "1\n"),
        ("tree", "3\n", "1\n"),
        ("blob", "3\n", "1\n"),
    ],
)
def test_annotated_tag_participates_in_existing_object_type_counts(
    tmp_path,
    monkeypatch,
    capsys,
    requested,
    expected_default,
    expected_filtered,
):
    repo, _commit, _tag, _tree, _blob = _repo(tmp_path)

    assert _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--count",
        f"--filter=object:type={requested}",
        "--missing=print-info",
        "v1",
    ) == expected_default

    assert _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--count",
        f"--filter=object:type={requested}",
        "--filter-provided-objects",
        "--missing=print-info",
        "v1",
    ) == expected_filtered
