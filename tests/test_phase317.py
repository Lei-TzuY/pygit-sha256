from __future__ import annotations

import subprocess

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.protocol_v2_unborn import (
    ProtocolV2LsRefsResult,
    parse_ls_refs_response_with_unborn,
)
from pygit.remote import Advertisement, pkt_line
from pygit.repo import Repository
from pygit.unborn_init import (
    EmptyRemoteInitializationError,
    initialize_empty_remote_head,
)


NATIVE_OID = "1" * 40
LOCAL_OID = "a" * 64


def _result(target: str = "refs/heads/main") -> ProtocolV2LsRefsResult:
    return ProtocolV2LsRefsResult(
        Advertisement(refs={}, capabilities=set(), symrefs={"HEAD": target}),
        frozenset({"HEAD"}),
    )


def _object_files(repo: Repository) -> list[str]:
    return sorted(
        str(path.relative_to(repo.store.root))
        for path in repo.store.root.rglob("*")
        if path.is_file()
    )


def test_initialize_empty_remote_head_sets_only_symbolic_head(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))

    branch = initialize_empty_remote_head(repo, _result("refs/heads/topic/empty"))

    assert branch == "topic/empty"
    assert repo.refs.get_head() == "ref: refs/heads/topic/empty"
    assert repo.refs.current_branch() == "topic/empty"
    assert repo.refs.resolve_head() is None
    assert repo.refs.list_branches() == []
    assert not (repo.pygit_dir / "refs" / "heads" / "topic" / "empty").exists()
    assert not (repo.pygit_dir / "logs" / "HEAD").exists()
    assert _object_files(repo) == []
    assert not (repo.pygit_dir / "promisor.json").exists()
    assert "0" * 64 not in (repo.pygit_dir / "HEAD").read_text(encoding="utf-8")


def test_initialize_empty_remote_head_composes_with_phase315_parser(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    capabilities = ProtocolV2Capabilities(
        {"ls-refs": "unborn", "fetch": "shallow", "object-format": "sha1"}
    )
    response = (
        pkt_line(b"unborn HEAD symref-target:refs/heads/main\n")
        + b"0000"
    )
    result = parse_ls_refs_response_with_unborn(response, capabilities)

    branch = initialize_empty_remote_head(repo, result)

    assert branch == "main"
    assert repo.refs.get_head() == "ref: refs/heads/main"
    assert repo.refs.resolve_head() is None
    assert repo.refs.list_branches() == []
    assert _object_files(repo) == []


def test_initialize_empty_remote_head_is_idempotent_without_reflog(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    result = _result("refs/heads/topic/empty")

    initialize_empty_remote_head(repo, result)
    first = (repo.pygit_dir / "HEAD").read_bytes()
    initialize_empty_remote_head(repo, result)

    assert (repo.pygit_dir / "HEAD").read_bytes() == first
    assert not (repo.pygit_dir / "logs" / "HEAD").exists()
    assert not (repo.pygit_dir / "HEAD.lock").exists()


def test_initialize_empty_remote_head_rejects_concrete_remote_refs_before_mutation(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    original = (repo.pygit_dir / "HEAD").read_bytes()
    result = ProtocolV2LsRefsResult(
        Advertisement(
            refs={"refs/heads/main": NATIVE_OID},
            capabilities=set(),
            symrefs={"HEAD": "refs/heads/main"},
        ),
        frozenset({"HEAD"}),
    )

    with pytest.raises(EmptyRemoteInitializationError, match="concrete remote refs"):
        initialize_empty_remote_head(repo, result)

    assert (repo.pygit_dir / "HEAD").read_bytes() == original
    assert _object_files(repo) == []


def test_initialize_empty_remote_head_rejects_concrete_and_unborn_head_conflict(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    original = (repo.pygit_dir / "HEAD").read_bytes()
    result = ProtocolV2LsRefsResult(
        Advertisement(
            refs={"HEAD": NATIVE_OID},
            capabilities=set(),
            symrefs={"HEAD": "refs/heads/main"},
        ),
        frozenset({"HEAD"}),
    )

    with pytest.raises(EmptyRemoteInitializationError, match="both concrete and unborn"):
        initialize_empty_remote_head(repo, result)

    assert (repo.pygit_dir / "HEAD").read_bytes() == original


@pytest.mark.parametrize(
    "target",
    [
        "refs/tags/main",
        "refs/heads/",
        "refs/heads/../escape",
        "refs/heads/topic//empty",
        "refs/heads/topic.lock",
        "refs/heads/.hidden",
        "refs/heads/topic..empty",
        "refs/heads/topic@{1}",
        "refs/heads/topic empty",
        "refs/heads/topic\\empty",
        "refs/heads/topic./empty",
    ],
)
def test_initialize_empty_remote_head_rejects_invalid_branch_target_before_mutation(
    tmp_path,
    target: str,
) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    original = (repo.pygit_dir / "HEAD").read_bytes()

    with pytest.raises(EmptyRemoteInitializationError):
        initialize_empty_remote_head(repo, _result(target))

    assert (repo.pygit_dir / "HEAD").read_bytes() == original
    assert not (repo.pygit_dir / "HEAD.lock").exists()


def test_initialize_empty_remote_head_requires_explicit_unborn_metadata(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    original = (repo.pygit_dir / "HEAD").read_bytes()
    result = ProtocolV2LsRefsResult(
        Advertisement(refs={}, capabilities=set(), symrefs={"HEAD": "refs/heads/main"}),
        frozenset(),
    )

    with pytest.raises(EmptyRemoteInitializationError, match="explicit unborn HEAD"):
        initialize_empty_remote_head(repo, result)

    assert (repo.pygit_dir / "HEAD").read_bytes() == original


def test_initialize_empty_remote_head_rejects_resolved_local_head(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    repo.refs.set_branch("main", LOCAL_OID, message="test")
    original = (repo.pygit_dir / "HEAD").read_bytes()

    with pytest.raises(EmptyRemoteInitializationError, match="resolved HEAD"):
        initialize_empty_remote_head(repo, _result("refs/heads/other"))

    assert (repo.pygit_dir / "HEAD").read_bytes() == original
    assert repo.refs.get_branch("main") == LOCAL_OID


def test_initialize_empty_remote_head_rejects_local_object_state_without_writing(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    oid = repo.store.write_raw(b"unreachable local object")
    original_head = (repo.pygit_dir / "HEAD").read_bytes()
    original_objects = _object_files(repo)

    with pytest.raises(EmptyRemoteInitializationError, match="local object state"):
        initialize_empty_remote_head(repo, _result("refs/heads/other"))

    assert (repo.pygit_dir / "HEAD").read_bytes() == original_head
    assert _object_files(repo) == original_objects
    assert any(path.replace("/", "") == oid for path in original_objects)


def test_initialize_empty_remote_head_never_touches_promisor_state(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    promisor = repo.pygit_dir / "promisor.json"
    promisor.write_text('{"objects": {"abc": {"type": "blob"}}}', encoding="utf-8")
    before = promisor.read_bytes()
    original_head = (repo.pygit_dir / "HEAD").read_bytes()

    with pytest.raises(EmptyRemoteInitializationError, match="promisor state"):
        initialize_empty_remote_head(repo, _result("refs/heads/other"))

    assert promisor.read_bytes() == before
    assert (repo.pygit_dir / "HEAD").read_bytes() == original_head


def test_initialize_empty_remote_head_fails_cleanly_if_head_lock_exists(tmp_path) -> None:
    repo = Repository.init(str(tmp_path / "clone"))
    lock = repo.pygit_dir / "HEAD.lock"
    lock.write_text("busy", encoding="utf-8")
    original = (repo.pygit_dir / "HEAD").read_bytes()

    with pytest.raises(EmptyRemoteInitializationError, match="lock already exists"):
        initialize_empty_remote_head(repo, _result("refs/heads/topic/empty"))

    assert (repo.pygit_dir / "HEAD").read_bytes() == original
    assert lock.read_text(encoding="utf-8") == "busy"


def test_native_git_empty_clone_keeps_target_branch_unborn(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    clone = tmp_path / "native-clone"
    subprocess.run(
        [
            "git",
            "init",
            "--bare",
            "--object-format=sha256",
            "--initial-branch=topic/empty",
            str(remote),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    completed = subprocess.run(
        ["git", "clone", str(remote), str(clone)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert b"cloned an empty repository" in completed.stderr
    assert (clone / ".git" / "HEAD").read_text(encoding="utf-8").strip() == (
        "ref: refs/heads/topic/empty"
    )
    symbolic = subprocess.run(
        ["git", "-C", str(clone), "symbolic-ref", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )
    assert symbolic.stdout.strip() == "refs/heads/topic/empty"
    show_ref = subprocess.run(
        ["git", "-C", str(clone), "show-ref"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert show_ref.returncode == 1
    assert show_ref.stdout == ""
    assert not (clone / ".git" / "refs" / "heads" / "topic" / "empty").exists()
    assert not (clone / ".git" / "logs" / "HEAD").exists()
    assert not any(path.is_file() for path in (clone / ".git" / "objects").rglob("*"))
