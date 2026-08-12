"""Integration tests for Phase 9 pygit features: Interactive Hunk Patch Staging & Git Bundle Engine."""

from pathlib import Path
import pytest
from pygit import Repository
from pygit.patch import parse_diff_hunks, apply_hunks_to_lines
from pygit.bundle import BundleEngine


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestPatchParsing:
    def test_parse_diff_hunks_and_apply(self):
        diff_text = (
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-line2_old\n"
            "+line2_new\n"
            " line3\n"
        )
        parsed = parse_diff_hunks(diff_text)
        assert len(parsed) == 1
        file_path, hunks = parsed[0]
        assert file_path == "app.py"
        assert len(hunks) == 1

        orig_lines = ["line1", "line2_old", "line3"]
        applied = apply_hunks_to_lines(orig_lines, hunks)
        assert applied == ["line1", "line2_new", "line3"]


class TestBundleEngine:
    def test_bundle_create_and_verify(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        c1 = _commit_file(repo, "f.txt", "1", "c1")
        c2 = _commit_file(repo, "f.txt", "2", "c2")

        bundle_path = tmp_path / "repo.bundle"
        created = repo.bundle_create(str(bundle_path))
        assert created.exists()

        info = repo.bundle_verify(str(bundle_path))
        assert info["status"] == "valid"
        assert len(info["refs"]) == 1
        assert list(info["refs"].values())[0] == c2
