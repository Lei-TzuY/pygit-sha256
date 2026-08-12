"""Integration tests for Phase 12 pygit features: Commit Signature Verifier & Terminal DAG History Visualizer."""

from pathlib import Path
import pytest
from pygit import Repository
from pygit.signature import parse_commit_signature


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestSignatureAndGraph:
    def test_parse_commit_signature_with_gpgsig(self):
        raw_commit_data = (
            b"tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904\n"
            b"parent 0000000000000000000000000000000000000000\n"
            b"author Alice <alice@example.com> 1700000000 +0000\n"
            b"committer Alice <alice@example.com> 1700000000 +0000\n"
            b"gpgsig -----BEGIN PGP SIGNATURE-----\n"
            b" Version: GnuPG v2\n"
            b" iQEcBAABAgAGBQJ...\n"
            b" -----END PGP SIGNATURE-----\n"
            b"\n"
            b"Signed commit message\n"
        )
        info = parse_commit_signature("dummy_sha", raw_commit_data)
        assert info.has_signature is True
        assert "-----BEGIN PGP SIGNATURE-----" in info.signature_block
        assert b"gpgsig" not in info.signed_payload

    def test_render_graph_ascii_layout(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        c1 = _commit_file(repo, "a.txt", "1", "c1")
        c2 = _commit_file(repo, "a.txt", "2", "c2")
        repo.tag("v1.0", c2)

        lines = repo.render_graph()
        assert len(lines) >= 2
        assert c2[:7] in lines[0]
        assert "HEAD -> main" in lines[0]
        assert "tag: v1.0" in lines[0]
