from __future__ import annotations

from pathlib import Path

import pygit.protocol_v2_packfile_uri_incremental_fetch as incremental
import pygit.protocol_v2_packfile_uri_refs as refpub
from pygit import Repository
from pygit.protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.refs import ZERO_SHA


NATIVE = "c" * 40


def _commit(repo: Repository) -> str:
    path = repo.worktree / "integration.txt"
    path.write_text("durable incremental publisher\n", encoding="utf-8")
    repo.add(["integration.txt"])
    return repo.commit("phase348 integration")


def test_incremental_fetch_seam_resolves_to_durable_default_publisher(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Phase347 intentionally keeps this module-level name monkeypatchable. Phase348
    # strengthens the function behind that same seam instead of renaming it.
    assert incremental.publish_packfile_uri_refs is refpub.publish_packfile_uri_refs

    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    certificate = PackfileUriRootCertificate({NATIVE: tip}, {NATIVE: b"commit"})
    publication = {
        "refs/remotes/origin/main": PackfileUriRefPublication(NATIVE, ZERO_SHA),
    }
    files: list[str] = []
    directories: list[str] = []

    monkeypatch.setattr(
        refpub,
        "_fsync_file",
        lambda path: files.append(path.relative_to(repo.pygit_dir).as_posix()),
    )
    monkeypatch.setattr(
        refpub,
        "_fsync_directory",
        lambda path: directories.append(path.relative_to(repo.pygit_dir).as_posix() or "."),
    )

    result = incremental.publish_packfile_uri_refs(
        repo,
        certificate,
        publication,
        message="fetch: phase348 integration",
    )

    assert result == {"refs/remotes/origin/main": tip}
    assert files == [
        "refs/remotes/origin/main",
        "logs/refs/remotes/origin/main",
    ]
    assert directories
    assert repo.refs.get_remote("origin", "main") == tip
