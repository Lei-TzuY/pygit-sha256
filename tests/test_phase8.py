"""Integration tests for Phase 8 pygit features: Git LFS Engine & SSH Remote Transport."""

from pathlib import Path
import pytest
from pygit import Repository
from pygit.lfs import LFSEngine
from pygit.remote_ssh import parse_ssh_url, SSHUrl


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestLFSEngine:
    def test_lfs_track_and_clean_filter(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        repo.lfs_track("*.bin")

        assert (tmp_path / ".pygitattributes").exists()

        # Add a mock large binary file
        payload = b"X" * 100000
        bin_path = tmp_path / "data.bin"
        bin_path.write_bytes(payload)

        repo.add(["data.bin"])
        c1 = repo.commit("add binary data")

        # Verify staged blob is an LFS pointer text file, not full payload
        entry = repo.index.entries["data.bin"]
        blob_obj = repo.store.read(entry.sha)
        pointer_text = blob_obj.data.decode("utf-8")

        lfs = LFSEngine(repo.pygit_dir, repo.worktree)
        assert lfs.is_pointer(pointer_text)
        oid, size = lfs.parse_pointer(pointer_text)
        assert size == 100000

        # Verify actual raw payload resides in .pygit/lfs/objects/
        stored_payload = lfs.read_payload(oid)
        assert stored_payload == payload


class TestSSHRemoteTransport:
    def test_parse_ssh_url_scp_format(self):
        res = parse_ssh_url("git@github.com:octocat/Hello-World.git")
        assert res == SSHUrl(user="git", host="github.com", port=None, path="octocat/Hello-World.git")

    def test_parse_ssh_url_standard_format(self):
        res = parse_ssh_url("ssh://admin@git.myserver.org:2222/var/repo.git")
        assert res == SSHUrl(user="admin", host="git.myserver.org", port=2222, path="var/repo.git")
