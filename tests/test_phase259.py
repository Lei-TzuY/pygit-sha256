from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject, TreeObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _ordinary_three_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    commits = []
    for index, name in enumerate(("a.txt", "b.txt", "c.txt"), start=1):
        (repo.worktree / name).write_text(f"{index}\n", encoding="utf-8")
        repo.add([name])
        commits.append(
            repo.commit(
                f"c{index}",
                author_name="Test",
                author_email="test@example.com",
                commit_date=str(index),
            )
        )
    return repo, tuple(commits)


def _tree_oid(repo: Repository, commit_sha: str) -> str:
    """Return a commit's root tree without touching child entry resolvers."""
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    return commit.tree.lower()


def _snapshot(repo: Repository, commit_sha: str) -> tuple[str, tuple[str, ...]]:
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    tree = repo.store.read(commit.tree)
    assert isinstance(tree, TreeObject)
    blobs = tuple(entry.sha.lower() for entry in sorted(tree.entries, key=lambda entry: entry.name))
    return commit.tree.lower(), blobs


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _tree_data(entries: tuple[tuple[str, str], ...]) -> bytes:
    return b"".join(
        f"100644 {name}\0".encode() + bytes.fromhex(oid)
        for name, oid in entries
    )


def _commit_data(tree_oid: str, *, parent: str | None = None, timestamp: int) -> bytes:
    parent_line = f"parent {parent}\n" if parent else ""
    return (
        f"tree {tree_oid}\n"
        f"{parent_line}"
        f"author Test <test@example.com> {timestamp} +0000\n"
        f"committer Test <test@example.com> {timestamp} +0000\n"
        "\nmsg\n"
    ).encode()


def _partial_two_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "partial"))
    repo.add_remote("origin", "https://example.test/repo.git")

    a_blob = _native_oid("blob", b"a\n")
    b_blob = _native_oid("blob", b"b\n")

    base_tree_data = _tree_data((("a.txt", a_blob),))
    base_tree = _native_oid("tree", base_tree_data)
    base_commit_data = _commit_data(base_tree, timestamp=1)
    base_commit = _native_oid("commit", base_commit_data)

    tip_tree_data = _tree_data((("a.txt", a_blob), ("b.txt", b_blob)))
    tip_tree = _native_oid("tree", tip_tree_data)
    tip_commit_data = _commit_data(tip_tree, parent=base_commit, timestamp=2)
    tip_commit = _native_oid("commit", tip_commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            base_tree: NativeObject("tree", base_tree_data, base_tree),
            base_commit: NativeObject("commit", base_commit_data, base_commit),
            tip_tree: NativeObject("tree", tip_tree_data, tip_tree),
            tip_commit: NativeObject("commit", tip_commit_data, tip_commit),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_base = importer.import_oid(base_commit)
    local_tip = importer.import_oid(tip_commit)
    repo.refs.set_branch("main", local_tip, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    return repo, local_base, local_tip, a_blob, b_blob


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("in-commit-order must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("in-commit-order must not batch-fetch"),
    )


def test_in_commit_order_interleaves_each_commit_snapshot(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    assert blobs1 == blobs2[:1] == blobs3[:1]
    assert blobs2 == blobs3[:2]
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "--no-object-names", "HEAD"]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        c3,
        tree3,
        *blobs3,
        c2,
        tree2,
        c1,
        tree1,
    ]


def test_in_commit_order_reverse_changes_first_seen_object_positions(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--reverse",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        c1,
        tree1,
        blobs1[0],
        c2,
        tree2,
        blobs2[1],
        c3,
        tree3,
        blobs3[2],
    ]


def test_in_commit_order_respects_max_count_but_walks_visible_snapshot(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _blobs1 = _snapshot(repo, c1)
    tree2, _blobs2 = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--max-count=2",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [c3, tree3, *blobs3, c2, tree2]
    assert c1 not in lines
    assert tree1 not in lines


def test_in_commit_order_print_info_keeps_promises_at_first_snapshot_position(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, a_blob, b_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    tip_tree = _tree_oid(repo, tip)
    base_tree = _tree_oid(repo, base)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--missing=print-info",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        tip,
        tip_tree,
        f"?{a_blob} path=a.txt type=blob",
        f"?{b_blob} path=b.txt type=blob",
        base,
        base_tree,
    ]
    assert read_promisor_state(repo.pygit_dir) == before


def test_in_commit_order_plain_print_and_count_preserve_missing_channel(
    tmp_path, monkeypatch, capsys
):
    repo, _base, _tip, a_blob, b_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--missing=print",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [f"?{a_blob}", f"?{b_blob}", "4"]
    assert read_promisor_state(repo.pygit_dir) == before


def test_in_commit_order_allow_promisor_omits_missing_without_fetch(tmp_path, monkeypatch, capsys):
    repo, base, tip, _a_blob, _b_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    tip_tree = _tree_oid(repo, tip)
    base_tree = _tree_oid(repo, base)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--missing=allow-promisor",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [tip, tip_tree, base, base_tree]
    assert read_promisor_state(repo.pygit_dir) == before


def test_in_commit_order_ordinary_partial_clone_fails_before_output(tmp_path, monkeypatch, capsys):
    repo, _base, _tip, _a_blob, _b_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    with pytest.raises(RuntimeError, match="use --missing=allow-promisor"):
        run_rev_list_disk_usage(["--objects", "--in-commit-order", "HEAD"])

    assert capsys.readouterr().out == ""
    assert read_promisor_state(repo.pygit_dir) == before


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--objects-edge"], "--objects-edge"),
        (["--filter-print-omitted"], "filter-print-omitted"),
    ],
)
def test_in_commit_order_rejects_unmodelled_presentation_combinations(
    tmp_path, monkeypatch, extra, message
):
    repo, _commits = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match=message):
        run_rev_list_disk_usage(["--objects", "--in-commit-order", *extra, "HEAD"])
