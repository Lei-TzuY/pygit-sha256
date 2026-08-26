"""Phase 140 tests: byte-faithful ``rev-list --header`` records."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import CommitObject, Identity, TreeObject
from pygit.objects.base import GitObject
from pygit.pack import PackWriter
from pygit.rev_list_header_cli import _raw_header_payload


class _RawCommit(GitObject):
    type_name = b"commit"

    def __init__(self, payload: bytes = b"") -> None:
        self.payload = payload

    def serialize(self) -> bytes:
        return self.payload

    def deserialize(self, data: bytes) -> None:
        self.payload = data


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _raw_commit(repo: Repository) -> _RawCommit:
    tree = repo.store.write(TreeObject([]))
    payload = (
        f"tree {tree}\n"
        "author A <a@example.com> 7 +0000\n"
        "committer C <c@example.com> 9 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n"
        " signed-line\n"
        " -----END PGP SIGNATURE-----\n"
        "x-phase140 preserve-me\n"
        "\n"
        "subject\n"
        "\n"
        "body\n"
    ).encode("utf-8")
    return _RawCommit(payload)


def _expected_body(payload: bytes) -> bytes:
    headers, message = payload.split(b"\n\n", 1)
    return headers + b"\n\n" + b"".join(b"    " + line for line in message.splitlines(keepends=True))


def test_raw_header_payload_preserves_headers_and_indents_every_message_line(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    raw = _raw_commit(repo)
    store_bytes = raw._build_store_bytes()

    rendered = _raw_header_payload(store_bytes)

    assert rendered == _expected_body(raw.payload)
    assert b"gpgsig -----BEGIN PGP SIGNATURE-----" in rendered
    assert b"x-phase140 preserve-me" in rendered
    assert b"    \n" in rendered


def test_cli_header_emits_exact_loose_raw_headers_and_nul_separator(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    raw = _raw_commit(repo)
    oid = repo.store.write(raw)
    repo.refs.set_branch("main", oid)
    repo.refs.set_head_symbolic("main")

    result = _run(repo, "rev-list", "--header", "HEAD")

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stderr == b""
    assert result.stdout == oid.encode() + b"\n" + _expected_body(raw.payload) + b"\x00"


def test_cli_header_reads_packed_only_commit_without_materializing_loose(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    raw = _raw_commit(repo)
    oid = raw.hash()
    PackWriter([(oid, raw)]).write_pack_and_idx(repo.store.root / "pack")
    repo.refs.set_branch("main", oid)
    repo.refs.set_head_symbolic("main")
    assert not repo.store._path_for(oid).exists()

    result = _run(repo, "rev-list", "--header", "HEAD")

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == oid.encode() + b"\n" + _expected_body(raw.payload) + b"\x00"
    assert not repo.store._path_for(oid).exists()


def test_cli_header_multiple_commits_are_nul_delimited(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tree = repo.store.write(TreeObject([]))
    ident1 = Identity("A", "a@example.com", 1, "+0000")
    ident2 = Identity("A", "a@example.com", 2, "+0000")
    root = repo.store.write(CommitObject(tree=tree, parents=[], author=ident1, committer=ident1, message="root\n"))
    tip = repo.store.write(CommitObject(tree=tree, parents=[root], author=ident2, committer=ident2, message="tip\n"))
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")

    result = _run(repo, "rev-list", "--header", "--topo-order", "HEAD")

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    records = result.stdout.split(b"\x00")
    assert records[-1] == b""
    assert records[0].startswith(tip.encode() + b"\n")
    assert records[1].startswith(root.encode() + b"\n")
    assert b"    tip\n" in records[0]
    assert b"    root\n" in records[1]


def test_cli_header_composes_with_timestamp_and_parents(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tree = repo.store.write(TreeObject([]))
    ident1 = Identity("A", "a@example.com", 11, "+0000")
    ident2 = Identity("A", "a@example.com", 22, "+0000")
    root = repo.store.write(CommitObject(tree=tree, parents=[], author=ident1, committer=ident1, message="root\n"))
    tip = repo.store.write(CommitObject(tree=tree, parents=[root], author=ident2, committer=ident2, message="tip\n"))
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")

    result = _run(repo, "rev-list", "--header", "--timestamp", "--parents", "-n", "1", "HEAD")

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.startswith(f"22 {tip} {root}\n".encode())
    assert result.stdout.endswith(b"\x00")


def test_cli_header_boundary_record_also_has_raw_body(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tree = repo.store.write(TreeObject([]))
    identities = [Identity("A", "a@example.com", value, "+0000") for value in (1, 2, 3)]
    root = repo.store.write(CommitObject(tree=tree, parents=[], author=identities[0], committer=identities[0], message="root\n"))
    middle = repo.store.write(CommitObject(tree=tree, parents=[root], author=identities[1], committer=identities[1], message="middle\n"))
    tip = repo.store.write(CommitObject(tree=tree, parents=[middle], author=identities[2], committer=identities[2], message="tip\n"))
    repo.refs.set_branch("root", root)
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")

    result = _run(repo, "rev-list", "--header", "--boundary", "root..main")

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    records = [record for record in result.stdout.split(b"\x00") if record]
    assert any(record.startswith(b"-" + root.encode() + b"\n") and b"    root\n" in record for record in records)


def test_cli_header_count_remains_plain_count(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    raw = _raw_commit(repo)
    oid = repo.store.write(raw)
    repo.refs.set_branch("main", oid)
    repo.refs.set_head_symbolic("main")

    result = _run(repo, "rev-list", "--header", "--count", "HEAD")

    assert result.returncode == 0
    assert result.stdout == b"1\n"
    assert b"\x00" not in result.stdout


def test_installed_help_lists_phase140_header_option(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    result = _run(repo, "rev-list", "--help")

    assert result.returncode == 0
    assert b"--header" in result.stdout
    assert b"--timestamp" in result.stdout
