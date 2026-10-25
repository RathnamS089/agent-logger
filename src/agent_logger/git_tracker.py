"""Git and filesystem change tracking for Agent Logger."""

import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set


def _run_git(*args, cwd: Optional[str] = None, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a git command and return the CompletedProcess.

    Args are passed as separate argv elements (never a single command string).
    """
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


def _git_lines(*args, cwd: Optional[str] = None) -> List[str]:
    """Run a git command and return non-empty stdout lines; [] on failure."""
    proc = _run_git(*args, cwd=cwd)
    if proc.returncode != 0:
        return []
    return [f for f in proc.stdout.splitlines() if f.strip()]


def _run_shell(cmd: str, cwd: Optional[str] = None, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run an arbitrary shell command."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


def init_repo_if_needed(repo_path: str) -> bool:
    """Ensure the repo path is a valid Git repo. Creates one if not."""
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        proc = _run_git("init", cwd=repo_path)
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to init git repo at {repo_path}")
    return True


def get_current_branch(repo_path: str) -> str:
    """Get the current branch name for a repo."""
    proc = _run_git("branch", "--show-current", cwd=repo_path)
    if proc.returncode != 0 or not proc.stdout.strip():
        proc = _run_git("symbolic-ref", "HEAD", cwd=repo_path)
        if proc.returncode != 0:
            # Detached HEAD: fall back to the short SHA.
            proc = _run_git("rev-parse", "--short", "HEAD", cwd=repo_path)
            if proc.returncode != 0:
                return "unknown"
            return proc.stdout.strip()
        return proc.stdout.strip().split("/")[-1]
    return proc.stdout.strip()


def _parse_numstat(lines: List[str]) -> Dict[str, Dict[str, int]]:
    """Parse `git diff --numstat` output into {path: {add, del}}."""
    line_changes: Dict[str, Dict[str, int]] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 3:
            try:
                add = int(parts[0]) if parts[0] != "-" else 0
                del_val = int(parts[1]) if parts[1] != "-" else 0
                fname = parts[2]
                if fname:
                    line_changes[fname] = {"add": add, "del": del_val}
            except (ValueError, IndexError):
                continue
    return line_changes


def get_diff_summary(repo_path: str, since_index: bool = True) -> Dict[str, Any]:
    """Get a diff summary of changes in the repo.

    Covers unstaged modifications, staged changes, and untracked files.
    """
    repo_path = os.path.abspath(repo_path)

    changed = _git_lines("diff", "--name-only", cwd=repo_path)
    staged = _git_lines("diff", "--cached", "--name-only", cwd=repo_path)
    untracked = _git_lines("ls-files", "--others", "--exclude-standard", cwd=repo_path)

    all_changed = set(changed) | set(staged) | set(untracked)

    stat_lines = _git_lines("diff", "--stat", cwd=repo_path)
    stat_line = stat_lines[-1] if stat_lines else ""

    numstat_lines = _git_lines("diff", "--numstat", cwd=repo_path)
    line_changes = _parse_numstat(numstat_lines)

    return {
        "changed_files": list(all_changed),
        "staged_files": list(set(staged)),
        "untracked_files": list(set(untracked)),
        "stat_line": stat_line,
        "line_changes": line_changes,
        "total_changed": len(all_changed),
    }


def get_new_since_last_session(
    repo_path: str,
    last_session_marker: Optional[str] = None,
) -> Dict[str, Any]:
    """Get files changed since a previous session marker.

    last_session_marker can be a commit hash, branch name, or tag.
    If None, compares working tree vs HEAD.
    """
    repo_path = os.path.abspath(repo_path)

    if last_session_marker:
        changed_since = _git_lines(
            "diff", f"{last_session_marker}...HEAD", "--name-only", cwd=repo_path
        )
    else:
        changed_since = _git_lines("diff", "HEAD", "--name-only", cwd=repo_path)

    untracked = _git_lines("ls-files", "--others", "--exclude-standard", cwd=repo_path)
    all_changed = set(changed_since) | set(untracked)

    line_changes: Dict[str, Dict[str, int]] = {}
    if all_changed:
        files = sorted(all_changed)[:50]
        numstat_lines = _git_lines("diff", "--numstat", "--", *files, cwd=repo_path)
        line_changes = _parse_numstat(numstat_lines)

    return {
        "changed_files": list(all_changed),
        "untracked_files": list(set(untracked)),
        "line_changes": line_changes,
        "total_changed": len(all_changed),
    }


class ChangeTracker:
    """Tracks file changes during a coding-agent session."""

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        init_repo_if_needed(self.repo_path)
        self.branch = get_current_branch(self.repo_path)
        self.session_start_snapshot: Optional[Dict[str, Any]] = None
        self.changed_since_snapshot: Set[str] = set()

    def snapshot(self) -> Dict[str, Any]:
        """Take a snapshot of the current git state."""
        diff = get_diff_summary(self.repo_path, since_index=True)
        self.session_start_snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "branch": self.branch,
            "changed_files": diff["changed_files"],
            "untracked_files": diff["untracked_files"],
            "line_changes": diff["line_changes"],
        }
        self.changed_since_snapshot = set(diff["changed_files"]) | set(
            diff["untracked_files"]
        )
        return self.session_start_snapshot

    def compare_since_snapshot(self) -> Dict[str, Any]:
        """Compare current state against the snapshot taken at session start."""
        if self.session_start_snapshot is None:
            self.snapshot()

        diff = get_diff_summary(self.repo_path, since_index=True)
        new_changed = set(diff["changed_files"]) | set(diff["untracked_files"])
        newly_changed = new_changed - self.changed_since_snapshot

        self.changed_since_snapshot = new_changed

        new_files = [f for f in diff["untracked_files"] if f in new_changed]
        return {
            "newly_changed_files": list(newly_changed),
            "all_changed_files": list(new_changed),
            "new_files": new_files,
            "summary": {
                "total_changed": len(new_changed),
                "newly_since_snapshot": len(newly_changed),
                "new_files_count": len(new_files),
            },
        }

    def get_session_files_summary(self) -> Dict[str, Any]:
        """Get a summary of files associated with the current session.

        Reflects the live working-tree state relative to the snapshot taken
        at session start (not the stale start-time snapshot).
        """
        if self.session_start_snapshot is None:
            return {
                "changed_files": [],
                "new_files": [],
                "total_changed": 0,
                "summary": "No snapshot taken yet.",
            }

        result = self.compare_since_snapshot()
        changed = result["all_changed_files"]
        new = result["new_files"]
        return {
            "changed_files": changed,
            "new_files": new,
            "total_changed": len(changed),
            "summary": f"{len(changed) - len(new)} changed, {len(new)} new",
        }
