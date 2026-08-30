from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject, Identity, TagObject
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
    for index, name in enumerate(("a.txt", "b.txt"), start=1):
        (repo.worktree / name).write_text(f"payload-{index}\n", encoding="utf-8")
        repo.add([name])
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


def _records(raw: str):
    if not raw:
        return []
    assert raw.endswith("\0")
    records = []
    current = None
    for field in raw[:-1].split("\0"):
        if "=" not in field:
            if current is not None:
                records.append(current)
            current = [field]
        else:
            assert current is not None
            current.append(field)
    if current is not None:
        records.append(current)
    return records


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("tag NUL filtering must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("tag NUL filtering must not batch-fetch"),
    )


def test_tag_nul_nested_chain_uses_path_metadata(tmp_path, monkeypatch, capsys):
    repo, (_c1, c2), tag1, tag2 = _ordinary_repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=tag",
        "v2",
    )

    assert _records(out) == [
        [c2],
        [tag2, "path=v2"],
        [tag1, "path=v1"],
    ]


def test_tag_nul_filter_provided_and_no_names_keep_only_tags(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, _c2), tag1, tag2 = _ordinary_repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--no-object-names",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "v2",
    )

    assert _records(out) == [[tag2], [tag1]]
    assert all(len(record[0]) == 64 for record in _records(out))


def test_tag_nul_plain_commit_root_matches_provided_policy(tmp_path, monkeypatch, capsys):
    repo, (_c1, c2), _tag1, _tag2 = _ordinary_repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=tag",
        "HEAD",
    )
    assert _records(out) == [[c2]]

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "HEAD",
    )
    assert out == ""


def test_tag_nul_count_stays_newline_framed(tmp_path, monkeypatch, capsys):
    repo, (_c1, _c2), _tag1, _tag2 = _ordinary_repo(tmp_path)

    assert _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--count",
        "--filter=object:type=tag",
        "v2",
    ) == "3\n"

    assert _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--count",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "v2",
    ) == "2\n"


def test_existing_object_type_nul_preserves_provided_tag(tmp_path, monkeypatch, capsys):
    repo, (c1, c2), tag1, _tag2 = _ordinary_repo(tmp_path)

    commit_out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=commit",
        "v1",
    )
    assert _records(commit_out) == [[c2], [c1], [tag1, "path=v1"]]

    tree_out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=tree",
        "v1",
    )
    tree_records = _records(tree_out)
    assert tree_records[0] == [c2]
    assert tree_records[1] == [tag1, "path=v1"]
    assert all(repo.store.read(record[0]).type_name == b"tree" for record in tree_records[2:])

    blob_out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=blob",
        "v1",
    )
    blob_records = _records(blob_out)
    assert blob_records[0] == [c2]
    assert blob_records[1] == [tag1, "path=v1"]
    assert all(repo.store.read(record[0]).type_name == b"blob" for record in blob_records[2:])


def test_existing_type_nul_filter_provided_removes_tag_exemption(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2), tag1, _tag2 = _ordinary_repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=tree",
        "--filter-provided-objects",
        "v1",
    )
    records = _records(out)
    assert all(record[0] not in {c2, tag1} for record in records)
    assert records
    assert all(repo.store.read(record[0]).type_name == b"tree" for record in records)


def test_tag_nul_boundary_commit_orders_tag_after_boundary(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2), tag1, _tag2 = _ordinary_repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "--boundary",
        "--max-count=1",
        "-z",
        "--filter=object:type=commit",
        "v1",
    )

    assert _records(out) == [
        [c2],
        [c1, "boundary=yes"],
        [tag1, "path=v1"],
    ]


def test_tag_nul_filter_print_omitted_remains_empty(tmp_path, monkeypatch, capsys):
    repo, (_c1, c2), tag1, _tag2 = _ordinary_repo(tmp_path)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=tag",
        "--filter-print-omitted",
        "v1",
    )

    assert _records(out) == [[c2], [tag1, "path=v1"]]
    assert "~" not in out


def test_partial_tag_nul_filters_missing_blob_before_validation_without_fetch(
    tmp_path, monkeypatch, capsys
):
    repo, local_commit, tag, native_blob = _partial_tag_repo(tmp_path)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=tag",
        "v1",
    )

    assert _records(out) == [[local_commit], [tag, "path=v1"]]
    assert native_blob not in out
    assert read_promisor_state(repo.pygit_dir) == before


def test_partial_blob_nul_inserts_local_tag_before_native_missing_record(
    tmp_path, monkeypatch, capsys
):
    repo, local_commit, tag, native_blob = _partial_tag_repo(tmp_path)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)

    out = _run(
        repo,
        monkeypatch,
        capsys,
        "--objects",
        "-z",
        "--filter=object:type=blob",
        "--missing=print-info",
        "v1",
    )

    assert _records(out) == [
        [local_commit],
        [tag, "path=v1"],
        [native_blob, "missing=yes", "path=f.txt", "type=blob"],
    ]
    assert len(local_commit) == 64 and len(tag) == 64 and len(native_blob) == 40
    assert read_promisor_state(repo.pygit_dir) == before


def test_tag_nul_still_rejects_objects_edge(tmp_path, monkeypatch, capsys):
    repo, (_c1, _c2), _tag1, _tag2 = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    with pytest.raises(ValueError, match="only compatible with --objects"):
        run_rev_list_disk_usage(
            [
                "--objects-edge",
                "-z",
                "--filter=object:type=tag",
                "v2",
            ]
        )
    assert capsys.readouterr().out == ""
