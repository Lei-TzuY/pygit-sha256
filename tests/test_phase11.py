"""Integration tests for Phase 11 pygit features: Packfile Verifier & Repository Object Counter Diagnostics."""

from pathlib import Path
import pytest
from pygit import Repository


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestDiagnosticsAndPackVerifier:
    def test_count_objects_and_verify_pack(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "a.txt", "line 1", "c1")
        _commit_file(repo, "b.txt", "line 2", "c2")

        info_loose = repo.count_objects()
        assert info_loose["count"] > 0
        assert info_loose["in_pack"] == 0

        # Repack into .pack / .idx
        pack_p, idx_p = repo.repack(delete_loose=True)

        info_packed = repo.count_objects()
        assert info_packed["count"] == 0
        assert info_packed["in_pack"] > 0
        assert info_packed["packs"] == 1

        # Verify packfile checksums and offsets
        entries = repo.verify_pack(str(idx_p), verbose=True)
        assert len(entries) == info_packed["in_pack"]
        for sha, t_name, size, compressed_size, offset in entries:
            assert len(sha) == 64
            assert size >= 0
            assert compressed_size > 0
            assert offset >= 12
