"""Integration tests for ignore, merge, and stash extensions."""

from pathlib import Path

from pygit import Repository
from pygit.objects import CommitObject


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestIgnore:
    def test_status_and_recursive_add_filter_pygitignore(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        (tmp_path / ".pygitignore").write_text(
            "*.log\nbuild/\n!build/keep.txt\n",
            encoding="utf-8",
        )
        (tmp_path / "debug.log").write_text("ignored")
        (tmp_path / "notes.txt").write_text("tracked")
        build = tmp_path / "build"
        build.mkdir()
        (build / "cache.bin").write_text("ignored")
        (build / "keep.txt").write_text("tracked")

        status = repo.status()
        assert "debug.log" not in status["untracked"]
        assert "build/cache.bin" not in status["untracked"]
        assert "notes.txt" in status["untracked"]
        assert "build/keep.txt" in status["untracked"]

        repo.add(["."])
        assert "debug.log" not in repo.index
        assert "build/cache.bin" not in repo.index
        assert "notes.txt" in repo.index
        assert "build/keep.txt" in repo.index

    def test_tracked_file_can_be_added_after_it_becomes_ignored(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        (tmp_path / "tracked.log").write_text("first")
        repo.add(["tracked.log"])
        repo.commit("base")
        (tmp_path / ".pygitignore").write_text("*.log\n")
        (tmp_path / "tracked.log").write_text("second")

        repo.add(["tracked.log"])

        assert ("modified", "tracked.log") in repo.status()["staged"]


class TestMerge:
    def test_three_way_merge_creates_two_parent_commit(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "base.txt", "base\n", "base")
        repo.branch("feature")
        _commit_file(repo, "feature.txt", "feature\n", "feature")
        repo.checkout("main")
        _commit_file(repo, "main.txt", "main\n", "main")

        result = repo.merge("feature")

        assert result["status"] == "merged"
        commit = repo.store.read(result["sha"])
        assert isinstance(commit, CommitObject)
        assert len(commit.parents) == 2
        assert (tmp_path / "feature.txt").read_text() == "feature\n"
        assert (tmp_path / "main.txt").read_text() == "main\n"

    def test_conflict_markers_can_be_resolved_and_committed(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "shared.txt", "base\n", "base")
        repo.branch("feature")
        _commit_file(repo, "shared.txt", "feature\n", "feature")
        repo.checkout("main")
        _commit_file(repo, "shared.txt", "main\n", "main")

        result = repo.merge("feature")

        assert result["status"] == "conflicts"
        content = (tmp_path / "shared.txt").read_text()
        assert "<<<<<<< HEAD" in content
        assert "=======" in content
        assert ">>>>>>> feature" in content
        assert repo.status()["conflicts"] == ["shared.txt"]

        (tmp_path / "shared.txt").write_text("resolved\n")
        repo.add(["shared.txt"])
        merge_sha = repo.commit("resolve")
        commit = repo.store.read(merge_sha)
        assert isinstance(commit, CommitObject)
        assert len(commit.parents) == 2
        assert repo.status()["conflicts"] == []

    def test_conflict_can_be_aborted(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "shared.txt", "base\n", "base")
        repo.branch("feature")
        _commit_file(repo, "shared.txt", "feature\n", "feature")
        repo.checkout("main")
        main = _commit_file(repo, "shared.txt", "main\n", "main")

        assert repo.merge("feature")["status"] == "conflicts"
        result = repo.merge_abort()

        assert result == {"status": "aborted", "sha": main, "conflicts": []}
        assert repo.refs.resolve_head() == main
        assert repo.status()["conflicts"] == []
        assert (tmp_path / "shared.txt").read_text() == "main\n"
        assert not (tmp_path / ".pygit" / "MERGE_HEAD").exists()
        assert not (tmp_path / ".pygit" / "MERGE_ORIG_HEAD").exists()

    def test_fast_forward_moves_current_branch(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "base.txt", "base\n", "base")
        repo.branch("feature")
        feature_sha = _commit_file(repo, "next.txt", "next\n", "next")
        repo.checkout("main")

        result = repo.merge("feature")

        assert result == {
            "status": "fast-forward",
            "sha": feature_sha,
            "conflicts": [],
        }
        assert repo.refs.get_branch("main") == feature_sha
        assert (tmp_path / "next.txt").exists()


class TestStash:
    def test_push_and_pop_restore_tracked_and_untracked_files(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "tracked.txt", "clean\n", "base")
        (tmp_path / "tracked.txt").write_text("dirty\n")
        (tmp_path / "new.txt").write_text("untracked\n")

        stash_sha = repo.stash_push("work in progress")

        assert repo.refs.get_stash() == stash_sha
        assert (tmp_path / "tracked.txt").read_text() == "clean\n"
        assert not (tmp_path / "new.txt").exists()
        assert repo.status()["unstaged"] == []
        assert repo.status()["untracked"] == []
        assert repo.stash_list()[0][1].message == "work in progress"

        assert repo.stash_pop() == stash_sha
        assert (tmp_path / "tracked.txt").read_text() == "dirty\n"
        assert (tmp_path / "new.txt").read_text() == "untracked\n"
        assert repo.refs.get_stash() is None
        assert ("modified", "tracked.txt") in repo.status()["unstaged"]
        assert "new.txt" in repo.status()["untracked"]


class TestReflog:
    def test_commit_and_checkout_record_head_movements(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        first = _commit_file(repo, "base.txt", "base\n", "base")
        repo.branch("feature")
        second = _commit_file(repo, "feature.txt", "feature\n", "feature")
        repo.checkout("main")

        entries = repo.reflog()

        assert entries[0].new_sha == first
        assert entries[0].message == "checkout: moving from feature to main"
        assert any(entry.new_sha == second and entry.message == "commit: feature" for entry in entries)
        assert any(
            entry.old_sha == first
            and entry.new_sha == first
            and entry.message == "checkout: moving from main to feature"
            for entry in entries
        )
        assert (tmp_path / ".pygit" / "logs" / "refs" / "heads" / "feature").exists()


class TestCherryPick:
    def test_replays_commit_on_current_branch(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        base = _commit_file(repo, "base.txt", "base\n", "base")
        repo.branch("feature")
        source = _commit_file(repo, "feature.txt", "feature\n", "feature")
        repo.checkout("main")

        result = repo.cherry_pick(source)

        assert result["status"] == "picked"
        assert (tmp_path / "feature.txt").read_text() == "feature\n"
        commit = repo.store.read(result["sha"])
        assert isinstance(commit, CommitObject)
        assert commit.parents == [base]

    def test_conflict_can_be_resolved_and_continued(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "shared.txt", "base\n", "base")
        repo.branch("feature")
        source = _commit_file(repo, "shared.txt", "feature\n", "feature")
        repo.checkout("main")
        main = _commit_file(repo, "shared.txt", "main\n", "main")

        result = repo.cherry_pick(source)

        assert result["status"] == "conflicts"
        assert repo.status()["conflicts"] == ["shared.txt"]
        (tmp_path / "shared.txt").write_text("resolved\n")
        repo.add(["shared.txt"])
        continued = repo.cherry_pick_continue()
        commit = repo.store.read(continued["sha"])
        assert isinstance(commit, CommitObject)
        assert commit.parents == [main]
        assert commit.message == "feature"


class TestRebase:
    def test_replays_divergent_commits_onto_target(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "base.txt", "base\n", "base")
        repo.branch("feature")
        original = _commit_file(repo, "feature.txt", "feature\n", "feature")
        repo.checkout("main")
        main = _commit_file(repo, "main.txt", "main\n", "main")
        repo.checkout("feature")

        result = repo.rebase("main")

        assert result["status"] == "rebased"
        assert result["sha"] != original
        assert repo.refs.get_branch("feature") == result["sha"]
        commit = repo.store.read(result["sha"])
        assert isinstance(commit, CommitObject)
        assert commit.parents == [main]
        assert (tmp_path / "feature.txt").read_text() == "feature\n"
        assert (tmp_path / "main.txt").read_text() == "main\n"

    def test_conflict_can_be_aborted(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "shared.txt", "base\n", "base")
        repo.branch("feature")
        original = _commit_file(repo, "shared.txt", "feature\n", "feature")
        repo.checkout("main")
        _commit_file(repo, "shared.txt", "main\n", "main")
        repo.checkout("feature")

        result = repo.rebase("main")

        assert result["status"] == "conflicts"
        aborted = repo.rebase_abort()
        assert aborted["sha"] == original
        assert repo.refs.get_branch("feature") == original
        assert (tmp_path / "shared.txt").read_text() == "feature\n"

    def test_conflict_can_be_resolved_and_continued(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "shared.txt", "base\n", "base")
        repo.branch("feature")
        _commit_file(repo, "shared.txt", "feature\n", "feature")
        repo.checkout("main")
        main = _commit_file(repo, "shared.txt", "main\n", "main")
        repo.checkout("feature")
        assert repo.rebase("main")["status"] == "conflicts"

        (tmp_path / "shared.txt").write_text("resolved\n")
        repo.add(["shared.txt"])
        result = repo.rebase_continue()

        assert result["status"] == "rebased"
        commit = repo.store.read(result["sha"])
        assert isinstance(commit, CommitObject)
        assert commit.parents == [main]
        assert commit.message == "feature"
        assert (tmp_path / "shared.txt").read_text() == "resolved\n"

    def test_conflict_can_be_skipped(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "shared.txt", "base\n", "base")
        repo.branch("feature")
        _commit_file(repo, "shared.txt", "feature\n", "feature")
        _commit_file(repo, "feature.txt", "feature file\n", "feature file")
        repo.checkout("main")
        main = _commit_file(repo, "shared.txt", "main\n", "main")
        repo.checkout("feature")
        assert repo.rebase("main")["status"] == "conflicts"

        result = repo.rebase_skip()

        assert result["status"] == "rebased"
        commit = repo.store.read(result["sha"])
        assert isinstance(commit, CommitObject)
        assert commit.parents == [main]
        assert commit.message == "feature file"
        assert (tmp_path / "shared.txt").read_text() == "main\n"
        assert (tmp_path / "feature.txt").read_text() == "feature file\n"
        assert repo.status()["conflicts"] == []


class TestBisect:
    def test_finds_first_bad_commit_and_resets_original_branch(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        commits = [
            _commit_file(repo, "value.txt", f"{value}\n", f"value {value}")
            for value in range(1, 6)
        ]

        started = repo.bisect_start(commits[4], commits[0])

        assert started == {
            "status": "testing",
            "sha": commits[2],
            "remaining": 4,
        }
        assert repo.refs.is_detached()
        assert (tmp_path / "value.txt").read_text() == "3\n"

        assert repo.bisect_good()["sha"] == commits[3]
        found = repo.bisect_bad()
        assert found == {"status": "found", "sha": commits[3]}
        assert (tmp_path / "value.txt").read_text() == "4\n"

        reset = repo.bisect_reset()
        assert reset == {"status": "reset", "sha": commits[4]}
        assert repo.refs.current_branch() == "main"
        assert (tmp_path / "value.txt").read_text() == "5\n"


class TestReset:
    def test_soft_reset_moves_head_and_keeps_index_and_worktree(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        first = _commit_file(repo, "value.txt", "one\n", "one")
        second = _commit_file(repo, "value.txt", "two\n", "two")

        result = repo.reset(first, mode="soft")

        assert result["old"] == second
        assert result["sha"] == first
        assert repo.refs.resolve_head() == first
        assert (tmp_path / "value.txt").read_text() == "two\n"
        assert ("modified", "value.txt") in repo.status()["staged"]

    def test_mixed_reset_resets_index_but_leaves_worktree(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        head = _commit_file(repo, "value.txt", "one\n", "one")
        (tmp_path / "value.txt").write_text("two\n")
        repo.add(["value.txt"])

        result = repo.reset("HEAD")

        assert result["mode"] == "mixed"
        assert repo.refs.resolve_head() == head
        assert repo.status()["staged"] == []
        assert ("modified", "value.txt") in repo.status()["unstaged"]
        assert (tmp_path / "value.txt").read_text() == "two\n"

    def test_hard_reset_restores_tracked_tree_and_preserves_untracked(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        first = _commit_file(repo, "value.txt", "one\n", "one")
        _commit_file(repo, "extra.txt", "tracked\n", "two")
        (tmp_path / "value.txt").write_text("dirty\n")
        (tmp_path / "scratch.txt").write_text("untracked\n")

        result = repo.reset(first, mode="hard")

        assert result["mode"] == "hard"
        assert repo.refs.resolve_head() == first
        assert (tmp_path / "value.txt").read_text() == "one\n"
        assert not (tmp_path / "extra.txt").exists()
        assert (tmp_path / "scratch.txt").read_text() == "untracked\n"
        assert repo.status()["staged"] == []
        assert repo.status()["unstaged"] == []
        assert repo.status()["untracked"] == ["scratch.txt"]

    def test_hard_reset_clears_merge_conflict_state(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        base = _commit_file(repo, "shared.txt", "base\n", "base")
        repo.branch("feature")
        _commit_file(repo, "shared.txt", "feature\n", "feature")
        repo.checkout("main")
        _commit_file(repo, "shared.txt", "main\n", "main")
        assert repo.merge("feature")["status"] == "conflicts"

        repo.reset(base, mode="hard")

        assert repo.status()["conflicts"] == []
        assert not (tmp_path / ".pygit" / "MERGE_HEAD").exists()
        assert not (tmp_path / ".pygit" / "MERGE_CONFLICTS").exists()
        assert (tmp_path / "shared.txt").read_text() == "base\n"

    def test_path_reset_updates_selected_index_entries_only(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        head = _commit_file(repo, "a.txt", "one\n", "one")
        _commit_file(repo, "b.txt", "one\n", "b")
        (tmp_path / "a.txt").write_text("two\n")
        (tmp_path / "b.txt").write_text("two\n")
        repo.add(["a.txt", "b.txt"])

        result = repo.reset_paths(["a.txt"], target="HEAD")

        assert result == {"status": "reset", "sha": repo.refs.resolve_head(), "paths": ["a.txt"]}
        assert repo.refs.resolve_head() != head
        assert ("modified", "a.txt") in repo.status()["unstaged"]
        assert ("modified", "b.txt") in repo.status()["staged"]
        assert (tmp_path / "a.txt").read_text() == "two\n"

    def test_path_reset_to_old_commit_removes_later_file_from_index(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        first = _commit_file(repo, "a.txt", "one\n", "one")
        _commit_file(repo, "later.txt", "later\n", "later")

        result = repo.reset_paths(["later.txt"], target=first)

        assert result == {"status": "reset", "sha": first, "paths": ["later.txt"]}
        assert "later.txt" not in repo.index
        assert (tmp_path / "later.txt").read_text() == "later\n"
        assert ("deleted", "later.txt") in repo.status()["staged"]
