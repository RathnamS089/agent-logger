"""Tests for state.py — SQLite persistence, serialization round-trips."""

import json
import os

import pytest

from agent_logger.state import AgentLoggerDB, SessionRecord, _parse_list


@pytest.fixture
def db(tmp_path):
    """AgentLoggerDB backed by a temp file."""
    return AgentLoggerDB(str(tmp_path / "test_sessions.db"))


class TestSessionRecord:
    def test_to_dict_round_trip(self):
        r = SessionRecord(
            session_id="s1", agent_name="a", repo_path="/repo", branch="main"
        )
        d = r.to_dict()
        r2 = SessionRecord.from_dict(d)
        assert r2.session_id == "s1"
        assert r2.agent_name == "a"
        assert r2.branch == "main"
        assert r2.status == "active"
        assert r2.start_time is not None
        assert r2.end_time is None

    def test_start_time_round_trips(self):
        from datetime import datetime, timezone

        r = SessionRecord("s1", "a", "/repo", "main")
        original = r.start_time.isoformat()
        r2 = SessionRecord.from_dict(r.to_dict())
        assert r2.start_time.isoformat() == original

    def test_files_lists_round_trip(self):
        r = SessionRecord("s1", "a", "/repo", "main")
        r.files_changed = ["a.py", "b.py"]
        r.new_files = ["c.py"]
        r2 = SessionRecord.from_dict(r.to_dict())
        assert r2.files_changed == ["a.py", "b.py"]
        assert r2.new_files == ["c.py"]
        assert r2.committed_files == []

    def test_duration_seconds_active(self):
        r = SessionRecord("s1", "a", "/repo", "main")
        assert r.duration_seconds is not None and r.duration_seconds >= 0

    def test_duration_seconds_completed(self):
        from datetime import datetime, timedelta, timezone

        r = SessionRecord("s1", "a", "/repo", "main")
        r.start_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        r.end_time = datetime.now(timezone.utc)
        assert 590 <= r.duration_seconds <= 610

    def test_summary_fields_round_trip(self):
        r = SessionRecord("s1", "a", "/repo", "main")
        r.summary = "did stuff"
        r.summary_source = "fallback"
        r2 = SessionRecord.from_dict(r.to_dict())
        assert r2.summary == "did stuff"
        assert r2.summary_source == "fallback"


class TestParseList:
    def test_json_array_string(self):
        assert _parse_list('["a.py","b.py"]') == ["a.py", "b.py"]

    def test_legacy_python_repr(self):
        assert _parse_list("['a.py', 'b.py']") == ["a.py", "b.py"]

    def test_real_list_passthrough(self):
        assert _parse_list(["x"]) == ["x"]

    def test_none_and_empty(self):
        assert _parse_list(None) == []
        assert _parse_list("") == []

    def test_invalid_string(self):
        assert _parse_list("not-a-list") == []


class TestAgentLoggerDB:
    def test_save_and_load(self, db):
        r = SessionRecord("s1", "claude", "/repo", "feat/x")
        r.files_changed = ["a.py"]
        db.save_session(r)
        loaded = db.load_session("s1")
        assert loaded is not None
        assert loaded.files_changed == ["a.py"]
        assert loaded.agent_name == "claude"
        assert loaded.branch == "feat/x"

    def test_load_missing_returns_none(self, db):
        assert db.load_session("nope") is None

    def test_list_sessions_ordering(self, db):
        from datetime import timedelta

        a = SessionRecord("s1", "a", "/repo", "main")
        b = SessionRecord("s2", "b", "/repo", "main")
        b.start_time = a.start_time + timedelta(minutes=5)
        db.save_session(a)
        db.save_session(b)
        sessions = db.list_sessions()
        assert sessions[0].session_id == "s2"  # newest first

    def test_get_active_session(self, db):
        a = SessionRecord("s1", "a", "/repo", "main", status="completed")
        b = SessionRecord("s2", "b", "/repo", "main", status="active")
        db.save_session(a)
        db.save_session(b)
        active = db.get_active_session()
        assert active is not None
        assert active.session_id == "s2"

    def test_get_active_session_none(self, db):
        assert db.get_active_session() is None

    def test_json_list_format_on_disk(self, db, tmp_path):
        r = SessionRecord("s1", "a", "/repo", "main")
        r.files_changed = ["a.py"]
        db.save_session(r)
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "test_sessions.db"))
        raw = conn.execute(
            "SELECT files_changed FROM sessions WHERE session_id='s1'"
        ).fetchone()[0]
        conn.close()
        assert json.loads(raw) == ["a.py"]

    def test_legacy_repr_rows_still_load(self, tmp_path):
        """Rows written by the old repr-format code must still load."""
        import sqlite3
        from datetime import datetime, timezone

        path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, agent_name TEXT, "
            "repo_path TEXT, branch TEXT, status TEXT, start_time TEXT, end_time TEXT, "
            "files_changed TEXT, new_files TEXT, committed_files TEXT)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('s1','a','/repo','main','active',?,?,"
            "'[''x.py'']','[]','[]')",
            (datetime.now(timezone.utc).isoformat(), None),
        )
        conn.commit()
        conn.close()
        db = AgentLoggerDB(path)
        s = db.load_session("s1")
        assert s is not None
        assert s.files_changed == ["x.py"]

    def test_migration_adds_summary_columns(self, tmp_path):
        """Old schema without summary columns gets them via ALTER TABLE."""
        import sqlite3

        path = str(tmp_path / "old_schema.db")
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, agent_name TEXT, "
            "repo_path TEXT, branch TEXT, status TEXT, start_time TEXT, end_time TEXT, "
            "files_changed TEXT, new_files TEXT, committed_files TEXT)"
        )
        conn.commit()
        conn.close()
        db = AgentLoggerDB(path)  # triggers migration
        r = SessionRecord("s1", "a", "/repo", "main")
        r.summary = "hello"
        db.save_session(r)
        loaded = db.load_session("s1")
        assert loaded.summary == "hello"

    def test_default_db_path_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_LOGGER_DB_PATH", str(tmp_path / "env.db"))
        db = AgentLoggerDB()
        assert db.db_path == str(tmp_path / "env.db")
        db.save_session(SessionRecord("s1", "a", "/r", "m"))
        assert os.path.exists(str(tmp_path / "env.db"))

    def test_creates_parent_dirs(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "sessions.db")
        db = AgentLoggerDB(nested)
        db.save_session(SessionRecord("s1", "a", "/r", "m"))
        assert os.path.exists(nested)
