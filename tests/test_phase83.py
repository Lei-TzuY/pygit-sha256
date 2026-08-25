"""Phase 83 tests: reflog-aware ``merge-base --fork-point`` discovery."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository, fork_point, merge_bases
from pygit.objects import CommitObject, TreeObject


def _repo(tmp_path: Path) -> tuple[Repository, str]:
    repo = Repository.init(str(tmp_path / "repo"))
    tree = repo.store.write(TreeObject())
    return repo, tree


def _commit(repo: Repository, tree: str, parents: list[str], label: str) -> str:
    return repo.store.write(CommitObject(tree=tree, parents=parents, message=label))


def _rewound_graph(tmp_path: Path):
    repo, tree = _repo(tmp_path)
    root = _commit(repo, tree, [], "root")

    # The topic was created from B0, an upstream incarnation that is later
    # discarded. The current upstream history starts again from root.
    b0 = _commit(repo, tree, [root], "B0")
    d0 = _commit(repo, tree, [b0], "D0")
    topic = _commit(repo, tree, [d0], "D")

    b1 = _commit(repo, tree, [root], "B1")
    b2 = _commit(repo, tree, [b1], "B2")
    current = _commit(repo, tree, [b2], "B")

    # Record every historical upstream tip in its reflog while ending at B.
    repo.refs.set_branch("upstream", b0, message="upstream B0")
    repo.refs.set_branch("upstream", b1, message="upstream B1")
    repo.refs.set_branch("upstream", b2, message="upstream B2")
    repo.refs.set_branch("upstream", current, message="upstream B")
    repo.refs.set_branch("topic", topic, message="topic")

    return repo, {
        "root": root,
        "b0": b0,
        "b1": b1,
        "b2": b2,
        "current": current,
        "d0": d0,
        "topic": topic,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "merge-base", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_fork_point_recovers_discarded_upstream_tip(tmp_path: Path) -> None:
    repo, ids = _rewound_graph(tmp_path)

    assert merge_bases(repo, "upstream", "topic") == [ids["root"]]
    assert fork_point(repo, "upstream", "topic") == ids["b0"]


def test_default_commit_uses_head(tmp_path: Path) -> None:
    repo, ids = _rewound_graph(tmp_path)
    repo.refs.set_head_symbolic("topic")

    assert fork_point(repo, "upstream") == ids["b0"]


def test_current_tip_works_without_a_reflog(tmp_path: Path) -> None:
    repo, tree = _repo(tmp_path)
    root = _commit(repo, tree, [], "root")
    upstream = _commit(repo, tree, [root], "upstream")
    topic = _commit(repo, tree, [upstream], "topic")
    repo.refs.set_branch("upstream", upstream)
    repo.refs.set_branch("topic", topic)

    log = repo.pygit_dir / "logs" / "refs" / "heads" / "upstream"
    log.unlink()

    assert fork_point(repo, "upstream", "topic") == upstream


def test_expired_reflog_candidate_does_not_fall_back_to_ordinary_base(tmp_path: Path) -> None:
    repo, ids = _rewound_graph(tmp_path)
    log = repo.pygit_dir / "logs" / "refs" / "heads" / "upstream"
    lines = log.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(lines) == 4

    # Expire only the entry whose new value was B0. The next retained record's
    # old OID still mentions B0, but B0 is no longer a retained reflog value and
    # must therefore not remain eligible as a fork-point candidate.
    log.write_text("".join(lines[1:]), encoding="utf-8")

    assert merge_bases(repo, "upstream", "topic") == [ids["root"]]
    assert fork_point(repo, "upstream", "topic") is None


def test_forking_from_non_tip_ancestor_is_not_a_fork_point(tmp_path: Path) -> None:
    repo, tree = _repo(tmp_path)
    root = _commit(repo, tree, [], "root")
    upstream_old = _commit(repo, tree, [root], "old")
    upstream_new = _commit(repo, tree, [upstream_old], "new")
    topic = _commit(repo, tree, [root], "topic-from-root")
    repo.refs.set_branch("upstream", upstream_old)
    repo.refs.set_branch("upstream", upstream_new)
    repo.refs.set_branch("topic", topic)

    assert merge_bases(repo, "upstream", "topic") == [root]
    assert fork_point(repo, "upstream", "topic") is None


def test_first_argument_must_be_an_actual_ref(tmp_path: Path) -> None:
    repo, ids = _rewound_graph(tmp_path)

    with pytest.raises(ValueError, match="requires a reference"):
        fork_point(repo, ids["current"], "topic")
    with pytest.raises(ValueError, match="requires a reference"):
        fork_point(repo, "upstream~1", "topic")


def test_malformed_reflog_fails_closed(tmp_path: Path) -> None:
    repo, _ = _rewound_graph(tmp_path)
    log = repo.pygit_dir / "logs" / "refs" / "heads" / "upstream"
    log.write_text("malformed reflog record\n", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed reflog"):
        fork_point(repo, "upstream", "topic")


def test_installed_cli_supports_explicit_and_default_commit(tmp_path: Path) -> None:
    repo, ids = _rewound_graph(tmp_path)

    explicit = _run(repo, "--fork-point", "upstream", "topic")
    assert explicit.returncode == 0, explicit.stderr
    assert explicit.stdout.strip() == ids["b0"]

    repo.refs.set_head_symbolic("topic")
    implicit = _run(repo, "--fork-point", "upstream")
    assert implicit.returncode == 0, implicit.stderr
    assert implicit.stdout.strip() == ids["b0"]


def test_installed_cli_returns_one_when_reflog_tip_expired(tmp_path: Path) -> None:
    repo, _ = _rewound_graph(tmp_path)
    log = repo.pygit_dir / "logs" / "refs" / "heads" / "upstream"
    lines = log.read_text(encoding="utf-8").splitlines(keepends=True)
    log.write_text("".join(lines[1:]), encoding="utf-8")

    result = _run(repo, "--fork-point", "upstream", "topic")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == ""


def test_cli_mode_validation_and_existing_modes_remain_available(tmp_path: Path) -> None:
    repo, ids = _rewound_graph(tmp_path)

    incompatible = _run(repo, "--fork-point", "--all", "upstream", "topic")
    assert incompatible.returncode == 2
    assert "cannot use --all" in incompatible.stderr

    too_many = _run(repo, "--fork-point", "upstream", "topic", "extra")
    assert too_many.returncode == 2
    assert "REF [COMMIT]" in too_many.stderr

    conflict = _run(repo, "--fork-point", "--octopus", "upstream", "topic")
    assert conflict.returncode == 2

    ordinary = _run(repo, "upstream", "topic")
    assert ordinary.returncode == 0, ordinary.stderr
    assert ordinary.stdout.strip() == ids["root"]

    ancestor = _run(repo, "--is-ancestor", ids["b0"], "topic")
    assert ancestor.returncode == 0

    help_result = _run(repo, "--help")
    assert help_result.returncode == 0
    assert "--fork-point" in help_result.stdout
