from __future__ import annotations

import pytest

from pygit.objects import Identity, TagObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _write_tag(repo: Repository, *, name: str, target: str, target_type: bytes) -> str:
    oid = repo.store.write(
        TagObject(
            target_sha=target,
            target_type=target_type,
            tag_name=name,
            tagger=Identity("Tagger", "tagger@example.com", 10, "+0000"),
            message=f"{name} annotation",
        )
    )
    path = repo.pygit_dir / "refs" / "tags" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(oid + "\n", encoding="ascii")
    return oid


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
    tag1 = _write_tag(repo, name="v1", target=commit, target_type=b"commit")
    tag2 = _write_tag(repo, name="v2", target=tag1, target_type=b"tag")
    return repo, commit, tag1, tag2


def _run(repo, monkeypatch, capsys, *args):
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()
    assert run_rev_list_disk_usage(list(args)) == 0
    return capsys.readouterr().out


def test_object_type_tag_preserves_positive_commit_and_prints_tag_name(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tag1, _tag2 = _repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--missing=print-info",
        "v1",
    )

    assert out.splitlines() == [commit, f"{tag1} v1"]


def test_object_type_tag_filter_provided_removes_peeled_commit_exemption(
    tmp_path, monkeypatch, capsys
):
    repo, _commit, tag1, _tag2 = _repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "--missing=print-info",
        "v1",
    )

    assert out.splitlines() == [f"{tag1} v1"]


def test_object_type_tag_nested_chain_is_outer_to_inner_for_explicit_revision(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tag1, tag2 = _repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--missing=print-info",
        "v2",
    )

    assert out.splitlines() == [commit, f"{tag2} v2", f"{tag1} v1"]


def test_object_type_tag_no_object_names_prints_only_local_sha256(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tag1, tag2 = _repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--missing=print-info",
        "--no-object-names",
        "v2",
    )

    assert out.splitlines() == [commit, tag2, tag1]
    assert all(len(line) == 64 for line in out.splitlines())


def test_object_type_tag_count_matches_provided_object_policy(
    tmp_path, monkeypatch, capsys
):
    repo, _commit, _tag1, _tag2 = _repo(tmp_path)

    assert _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--count",
        "--filter=object:type=tag",
        "--missing=print-info",
        "v2",
    ) == "3\n"

    assert _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--count",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "--missing=print-info",
        "v2",
    ) == "2\n"


def test_object_type_tag_all_refs_deduplicates_nested_chain(tmp_path, monkeypatch, capsys):
    repo, commit, tag1, tag2 = _repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--all",
        "--filter=object:type=tag",
        "--missing=print-info",
    )

    assert out.splitlines() == [commit, f"{tag1} v1", f"{tag2} v2"]


def test_object_type_tag_peeled_expression_does_not_reintroduce_tag(
    tmp_path, monkeypatch, capsys
):
    repo, commit, _tag1, _tag2 = _repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--missing=print-info",
        "v1^{}",
    )
    assert out.splitlines() == [commit]

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "--missing=print-info",
        "v1^{}",
    )
    assert out == ""


def test_object_type_tag_filter_print_omitted_stays_empty(tmp_path, monkeypatch, capsys):
    repo, commit, tag1, _tag2 = _repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--filter=object:type=tag",
        "--filter-print-omitted",
        "--missing=print-info",
        "v1",
    )

    assert out.splitlines() == [commit, f"{tag1} v1"]
    assert "~" not in out


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--in-commit-order",), "--in-commit-order is not yet supported"),
    ],
)
def test_object_type_tag_retains_explicit_followup_guards(
    tmp_path, monkeypatch, extra, message
):
    repo, _commit, _tag1, _tag2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match=message):
        run_rev_list_disk_usage(
            [
                "--objects",
                *extra,
                "--filter=object:type=tag",
                "--missing=print-info",
                "v1",
            ]
        )
