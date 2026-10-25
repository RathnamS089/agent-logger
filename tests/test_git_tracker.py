"""Tests for git_tracker.py — real git in temp repos + mocked subprocess."""

import subprocess
from unittest.mock import patch

import pytest

from agent_logger.git_tracker import (
    ChangeTracker,
    _run_git,
    get_current_branch,
    get_diff_summary,
    get_new_since_last_session,
    init_repo_if_needed,
)


def _git(repo, *args):
    r = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A real git repo with one commit, main branch."""
    p = tmp_path / "repo"
    p.mkdir()
    _git(p, "init", "-b", "main")
    _git(p, "config", "user.email", "t@t.co")
    _git(p, "config", "user.name", "t")
    (p / "base.txt").write_text("base\n")
    _git(p, "add", ".")
    _git(p, "commit", "-m", "init")
    return p


class TestHelpers:
    def test_init_repo_if_needed_creates(self, tmp_path):
        p = tmp_path / "fresh"
        p.mkdir()
        assert init_repo_if_needed(str(p)) is True
        assert (p / ".git").is_dir()

    def test_init_repo_if_needed_idempotent(self, repo):
        assert init_repo_if_needed(str(repo)) is True

    def test_get_current_branch(self, repo):
        assert get_current_branch(str(repo)) == "main"

    def test_get_current_branch_detached(self, repo):
        sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", sha)
        assert get_current_branch(str(repo)) == sha[:7] or get_current_branch(str(repo)) != "main"

    def test_run_git_timeout_error(self):
        with patch("agent_logger.git_tracker.subprocess.run") as m:
            m.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
            with pytest.raises(subprocess.TimeoutExpired):
                _run_git("status")

    def test_get_diff_summary_git_failure_paths(self, tmp_path):
        """Non-git dir → all-git calls fail → empty lists, no exception."""
        d = tmp_path / "notarepo"
        d.mkdir()
        result = get_diff_summary(str(d))
        assert result["changed_files"] == []
        assert result["total_changed"] == 0


class TestDiffSummary:
    def test_clean_repo(self, repo):
        result = get_diff_summary(str(repo))
        assert result["total_changed"] == 0
        assert result["changed_files"] == []

    def test_modified_file(self, repo):
        (repo / "base.txt").write_text("changed\n")
        result = get_diff_summary(str(repo))
        assert "base.txt" in result["changed_files"]

    def test_staged_file(self, repo):
        (repo / "staged.py").write_text("x\n")
        _git(repo, "add", "staged.py")
        result = get_diff_summary(str(repo))
        assert "staged.py" in result["changed_files"]
        assert "staged.py" in result["staged_files"]

    def test_untracked_file(self, repo):
        (repo / "new.py").write_text("x\n")
        result = get_diff_summary(str(repo))
        assert "new.py" in result["untracked_files"]
        assert "new.py" in result["changed_files"]

    def test_numstat_counts(self, repo):
        (repo / "base.txt").write_text("one\ntwo\nthree\n")
        result = get_diff_summary(str(repo))
        assert result["line_changes"].get("base.txt") == {"add": 3, "del": 1}

    def test_nested_path(self, repo):
        sub = repo / "src"
        sub.mkdir()
        (sub / "deep.py").write_text("x\n")
        result = get_diff_summary(str(repo))
        assert "src/deep.py" in result["changed_files"]


class TestGetNewSinceLastSession:
    def test_vs_head(self, repo):
        (repo / "mod.py").write_text("x\n")
        result = get_new_since_last_session(str(repo))
        assert "mod.py" in result["changed_files"]

    def test_with_marker(self, repo):
        (repo / "m1.py").write_text("a\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "c1")
        (repo / "m2.py").write_text("b\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "c2")
        result = get_new_since_last_session(str(repo), last_session_marker="main~1")
        assert "m2.py" in result["changed_files"]
        assert "m1.py" not in result["changed_files"]


class TestChangeTracker:
    def test_snapshot_and_compare(self, repo):
        t = ChangeTracker(str(repo))
        t.snapshot()
        (repo / "later.py").write_text("x\n")
        result = t.compare_since_snapshot()
        assert "later.py" in result["all_changed_files"]
        assert "later.py" in result["newly_changed_files"]

    def test_compare_without_snapshot_takes_one(self, repo):
        t = ChangeTracker(str(repo))
        result = t.compare_since_snapshot()  # no explicit snapshot call
        assert "all_changed_files" in result

    def test_summary_counts(self, repo):
        t = ChangeTracker(str(repo))
        t.snapshot()
        (repo / "a.py").write_text("x\n")
        (repo / "b.py").write_text("y\n")
        result = t.compare_since_snapshot()
        assert result["summary"]["total_changed"] == 2
        assert result["summary"]["new_files_count"] == 2

    def test_get_session_files_summary_no_snapshot(self, repo):
        t = ChangeTracker(str(repo))
        result = t.get_session_files_summary()
        assert result["total_changed"] == 0
        assert "No snapshot" in result["summary"]

    def test_get_session_files_summary_with_snapshot(self, repo):
        t = ChangeTracker(str(repo))
        t.snapshot()
        (repo / "z.py").write_text("x\n")
        result = t.get_session_files_summary()
        assert result["total_changed"] >= 1
