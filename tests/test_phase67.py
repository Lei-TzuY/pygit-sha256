"""Phase 67 tests for pack-objects selection and round-trip output."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pygit import Repository, pack_objects, parse_pack_bytes, select_pack_objects, unpack_objects
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject
from pygit.pack import PackReader
from pygit.pack_objects_cli import run_pack_objects
from pygit.pack_verifier import verify_packfile


def _history(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    ident = Identity("Tester", "tester@example.com", timestamp=1, timezone="+0000")

    root_blob = BlobObject(b"root\n")
    root_blob_oid = repo.store.write(root_blob)
    root_tree = TreeObject([TreeEntry("100644", "file.txt", root_blob_oid)])
    root_tree_oid = repo.store.write(root_tree)
    root = CommitObject(
        tree=root_tree_oid,
        parents=[],
        author=ident,
        committer=ident,
        message="root",
    )
    root_oid = repo.store.write(root)

    tip_blob = BlobObject(b"tip\n")
    tip_blob_oid = repo.store.write(tip_blob)
    tip_tree = TreeObject([TreeEntry("100644", "file.txt", tip_blob_oid)])
    tip_tree_oid = repo.store.write(tip_tree)
    tip = CommitObject(
        tree=tip_tree_oid,
        parents=[root_oid],
        author=ident,
        committer=ident,
        message="tip",
    )
    tip_oid = repo.store.write(tip)
    repo.refs.set_branch("main", tip_oid)

    return repo, {
        "root_blob": root_blob_oid,
        "root_tree": root_tree_oid,
        "root": root_oid,
        "tip_blob": tip_blob_oid,
        "tip_tree": tip_tree_oid,
        "tip": tip_oid,
    }


def test_exact_object_file_pack_round_trips_through_existing_readers(tmp_path: Path) -> None:
    repo, ids = _history(tmp_path)
    prefix = tmp_path / "out" / "bundle"

    result = pack_objects(repo, [ids["tip_blob"]], output_prefix=prefix)

    assert result.object_count == 1
    assert result.oids == (ids["tip_blob"],)
    assert result.pack_path is not None and result.pack_path.exists()
    assert result.idx_path is not None and result.idx_path.exists()
    assert result.pack_path.name.startswith("bundle-")
    assert len(result.pack_hash) == 40

    reader = PackReader(result.idx_path)
    assert reader.get_shas() == [ids["tip_blob"]]
    assert reader.read_object(ids["tip_blob"]).serialize() == b"tip\n"
    assert verify_packfile(result.idx_path)[0][0] == ids["tip_blob"]


def test_revs_walk_and_negative_revision_subtract_reachable_closure(tmp_path: Path) -> None:
    repo, ids = _history(tmp_path)

    selected = select_pack_objects(
        repo,
        [ids["tip"], "^" + ids["root"]],
        revs=True,
    )

    assert set(selected) == {ids["tip"], ids["tip_tree"], ids["tip_blob"]}


def test_annotated_tag_walk_includes_tag_target_and_commit_closure(tmp_path: Path) -> None:
    repo, ids = _history(tmp_path)
    ident = Identity("Tester", "tester@example.com", timestamp=2, timezone="+0000")
    tag = TagObject(
        target_sha=ids["tip"],
        target_type=b"commit",
        tag_name="v1",
        tagger=ident,
        message="release",
    )
    tag_oid = repo.store.write(tag)

    selected = select_pack_objects(repo, [tag_oid], revs=True)

    assert tag_oid in selected
    assert ids["tip"] in selected
    assert ids["root"] in selected
    assert ids["tip_blob"] in selected


def test_shallow_commit_stops_parent_walk_but_keeps_its_tree(tmp_path: Path) -> None:
    repo, ids = _history(tmp_path)
    (repo.pygit_dir / "shallow").write_text(ids["tip"] + "\n", encoding="utf-8")

    selected = select_pack_objects(repo, [ids["tip"]], revs=True)

    assert set(selected) == {ids["tip"], ids["tip_tree"], ids["tip_blob"]}
    assert ids["root"] not in selected


def test_all_refs_walk_excludes_unreachable_dangling_objects(tmp_path: Path) -> None:
    repo, ids = _history(tmp_path)
    dangling = repo.store.write(BlobObject(b"dangling\n"))

    selected = select_pack_objects(repo, all_refs=True)

    assert ids["tip"] in selected
    assert ids["root"] in selected
    assert ids["tip_blob"] in selected
    assert dangling not in selected


def test_stdout_pack_is_binary_round_trip_and_creates_no_persistent_pack(tmp_path: Path) -> None:
    repo, ids = _history(tmp_path)

    result = pack_objects(repo, [ids["tip"]], revs=True, stdout=True)

    assert result.pack_path is None
    assert result.idx_path is None
    assert result.pack_data is not None
    parsed = parse_pack_bytes(result.pack_data)
    assert {entry.oid for entry in parsed.entries} == set(result.oids)

    pack_path = tmp_path / "stream.pack"
    pack_path.write_bytes(result.pack_data)
    target = Repository.init(str(tmp_path / "target"))
    unpacked = unpack_objects(target, pack_path)
    assert set(unpacked.oids) == set(result.oids)
    assert set(target.store.all_shas()) == set(result.oids)


def test_cli_file_mode_reads_stdin_and_prints_pack_hash(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, ids = _history(tmp_path)
    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr("sys.stdin", io.StringIO(ids["tip_blob"] + "\n"))
    prefix = tmp_path / "cli" / "objects"

    assert run_pack_objects([str(prefix)]) == 0

    printed = capsys.readouterr().out.strip()
    assert len(printed) == 40
    packs = list((tmp_path / "cli").glob("objects-*.pack"))
    indexes = list((tmp_path / "cli").glob("objects-*.idx"))
    assert len(packs) == 1
    assert len(indexes) == 1


def test_negative_revision_requires_graph_mode_and_positive_root(tmp_path: Path) -> None:
    repo, ids = _history(tmp_path)

    with pytest.raises(ValueError, match="negative revisions require"):
        select_pack_objects(repo, ["^" + ids["root"]])
    with pytest.raises(ValueError, match="positive object"):
        select_pack_objects(repo, ["^" + ids["root"]], revs=True)
