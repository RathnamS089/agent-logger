"""End-to-end CLI tests: start → edit files → end → status → changes."""

import os
import subprocess

import pytest

from agent_logger import cli
from agent_logger.state import AgentLoggerDB


def _git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "repo"
    p.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=p, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.co"], cwd=p, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=p, capture_output=True, check=True
    )
    (p / "base.txt").write_text("base\n")
    _git(p, "add", ".")
    _git(p, "commit", "-m", "init")
    return p


@pytest.fixture
def env(tmp_path, repo, monkeypatch):
    """Point the DB at a temp file and run inside the temp repo."""
    monkeypatch.setenv("AGENT_LOGGER_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.delenv("AGENT_LOGGER_MODEL", raising=False)
    monkeypatch.chdir(repo)
    return repo


class TestStartEndFlow:
    def test_start_creates_active_session(self, env, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv", ["agent-logger", "start", str(env), "--agent", "test-agent"]
        )
        cli.main()
        out = capsys.readouterr().out
        assert "Started session" in out
        assert "test-agent" in out

        db = AgentLoggerDB(str(os.environ["AGENT_LOGGER_DB_PATH"]))
        active = db.get_active_session()
        assert active is not None
        assert active.status == "active"
        assert active.agent_name == "test-agent"
        assert active.branch == "main"

    def test_full_flow_persists_summary(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-logger", "start", str(env)])
        cli.main()
        (env / "feature.py").write_text("x = 1\n")
        (env / "base.txt").write_text("edited\n")
        monkeypatch.setattr("sys.argv", ["agent-logger", "end"])
        cli.main()
        out = capsys.readouterr().out
        assert "Ended session" in out
        assert "Summary:" in out

        db = AgentLoggerDB(str(os.environ["AGENT_LOGGER_DB_PATH"]))
        sessions = db.list_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s.status == "completed"
        assert s.end_time is not None
        assert "feature.py" in s.files_changed
        assert "base.txt" in s.files_changed
        assert "feature.py" in s.new_files
        assert s.summary is not None and len(s.summary) > 0

    def test_end_without_start_is_noop(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-logger", "end"])
        cli.main()
        assert "No active session" in capsys.readouterr().out

    def test_start_on_missing_path_errors(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-logger", "start", "/nonexistent/xyz"])
        with pytest.raises(SystemExit):
            cli.main()

    def test_default_dot_uses_cwd(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-logger", "start"])
        cli.main()
        assert "Started session" in capsys.readouterr().out


class TestStatusAndChanges:
    def test_status_lists_sessions(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-logger", "start", str(env)])
        cli.main()
        monkeypatch.setattr("sys.argv", ["agent-logger", "end"])
        cli.main()
        monkeypatch.setattr("sys.argv", ["agent-logger", "status"])
        cli.main()
        out = capsys.readouterr().out
        assert "Recent sessions" in out
        assert "completed" in out

    def test_changes_shows_persisted_summary(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-logger", "start", str(env)])
        cli.main()
        (env / "thing.py").write_text("v\n")
        monkeypatch.setattr("sys.argv", ["agent-logger", "end"])
        cli.main()
        capsys.readouterr()
        monkeypatch.setattr("sys.argv", ["agent-logger", "changes"])
        cli.main()
        out = capsys.readouterr().out
        assert "Change summary" in out
        assert "thing.py" in out or "1" in out

    def test_changes_empty_db(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-logger", "changes"])
        cli.main()
        assert "No sessions found" in capsys.readouterr().out

    def test_model_failure_uses_fallback(self, env, monkeypatch, capsys):
        """With a model configured but Ollama unreachable, fallback is used."""
        monkeypatch.setenv("AGENT_LOGGER_MODEL", "ollama:phi")
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")  # nothing there
        monkeypatch.setattr("sys.argv", ["agent-logger", "start", str(env)])
        cli.main()
        (env / "f.py").write_text("x\n")
        monkeypatch.setattr("sys.argv", ["agent-logger", "end"])
        cli.main()
        out = capsys.readouterr().out
        assert "Summary:" in out

        monkeypatch.setattr("sys.argv", ["agent-logger", "changes"])
        cli.main()
        out = capsys.readouterr().out
        assert "Change summary" in out


class TestEdgeCases:
    def test_end_records_new_files(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-logger", "start", str(env)])
        cli.main()
        (env / "created_during_session.py").write_text("1\n")
        monkeypatch.setattr("sys.argv", ["agent-logger", "end"])
        cli.main()
        db = AgentLoggerDB(str(os.environ["AGENT_LOGGER_DB_PATH"]))
        s = db.list_sessions()[0]
        assert "created_during_session.py" in s.new_files
        assert "created_during_session.py" in s.files_changed

    def test_two_sessions_same_repo(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-logger", "start", str(env)])
        cli.main()
        (env / "s1.py").write_text("1\n")
        monkeypatch.setattr("sys.argv", ["agent-logger", "end"])
        cli.main()
        monkeypatch.setattr("sys.argv", ["agent-logger", "start", str(env)])
        cli.main()
        (env / "s2.py").write_text("2\n")
        monkeypatch.setattr("sys.argv", ["agent-logger", "end"])
        cli.main()
        db = AgentLoggerDB(str(os.environ["AGENT_LOGGER_DB_PATH"]))
        sessions = db.list_sessions()
        assert len(sessions) == 2
        assert all(s.status == "completed" for s in sessions)

    def test_agent_name_from_env(self, env, monkeypatch, capsys):
        monkeypatch.setenv("AGENT_LOGGER_AGENT_NAME", "env-agent")
        monkeypatch.setattr("sys.argv", ["agent-logger", "start", str(env)])
        cli.main()
        assert "env-agent" in capsys.readouterr().out
