"""Core session tracking state and persistence for Agent Logger."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO datetime string (or pass through a datetime) safely."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _parse_list(value: Any) -> List[str]:
    """Parse a stored file list. Accepts JSON arrays, Python-style list
    strings (legacy repr format), and real lists."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
        # Legacy fallback: Python repr like "['a.py', 'b.py']"
        if text.startswith("[") and text.endswith("]"):
            inner = text[1:-1].strip()
            if not inner:
                return []
            return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
    return []


class SessionRecord:
    """Represents a single coding-agent session record."""

    def __init__(
        self,
        session_id: str,
        agent_name: str,
        repo_path: str,
        branch: str,
        status: str = "active",
    ):
        self.session_id = session_id
        self.agent_name = agent_name
        self.repo_path = str(repo_path)
        self.branch = branch
        self.status = status
        self.start_time = datetime.now(timezone.utc)
        self.end_time: Optional[datetime] = None
        self.files_changed: List[str] = []
        self.new_files: List[str] = []
        self.committed_files: List[str] = []
        self.summary: Optional[str] = None
        self.summary_source: Optional[str] = None

    @property
    def duration_seconds(self) -> Optional[int]:
        """Session duration in seconds, measured to end_time (or now if active)."""
        if not self.start_time:
            return None
        ref = self.end_time or datetime.now(timezone.utc)
        start = self.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        return max(0, int((ref - start).total_seconds()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "repo_path": self.repo_path,
            "branch": self.branch,
            "status": self.status,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "files_changed": list(self.files_changed),
            "new_files": list(self.new_files),
            "committed_files": list(self.committed_files),
            "summary": self.summary,
            "summary_source": self.summary_source,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionRecord":
        r = cls(
            session_id=d["session_id"],
            agent_name=d["agent_name"],
            repo_path=d["repo_path"],
            branch=d.get("branch", "main") or "main",
            status=d.get("status", "active") or "active",
        )
        start = _parse_dt(d.get("start_time"))
        if start is not None:
            r.start_time = start
        r.end_time = _parse_dt(d.get("end_time"))
        r.files_changed = _parse_list(d.get("files_changed"))
        r.new_files = _parse_list(d.get("new_files"))
        r.committed_files = _parse_list(d.get("committed_files"))
        summary = d.get("summary")
        r.summary = str(summary) if summary else None
        source = d.get("summary_source")
        r.summary_source = str(source) if source else None
        return r


class AgentLoggerDB:
    """SQLite-backed session storage for Agent Logger."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("AGENT_LOGGER_DB_PATH", "sessions.db")
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            parent = os.path.dirname(os.path.abspath(self.db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self):
        conn = self._ensure_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                repo_path TEXT NOT NULL,
                branch TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                start_time TEXT NOT NULL,
                end_time TEXT,
                files_changed TEXT DEFAULT '[]',
                new_files TEXT DEFAULT '[]',
                committed_files TEXT DEFAULT '[]',
                summary TEXT,
                summary_source TEXT
            )
            """
        )
        # Migration for databases created before summary columns existed.
        existing_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        for col in ("summary", "summary_source"):
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
            )
            """
        )
        conn.commit()

    def save_session(self, record: SessionRecord):
        conn = self._ensure_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO sessions
            (session_id, agent_name, repo_path, branch, status, start_time, end_time,
             files_changed, new_files, committed_files, summary, summary_source)
            VALUES
            (:session_id, :agent_name, :repo_path, :branch, :status, :start_time, :end_time,
             :files_changed, :new_files, :committed_files, :summary, :summary_source)
            """,
            {
                "session_id": record.session_id,
                "agent_name": record.agent_name,
                "repo_path": record.repo_path,
                "branch": record.branch,
                "status": record.status,
                "start_time": record.start_time.isoformat() if record.start_time else None,
                "end_time": record.end_time.isoformat() if record.end_time else None,
                "files_changed": json.dumps(list(record.files_changed)),
                "new_files": json.dumps(list(record.new_files)),
                "committed_files": json.dumps(list(record.committed_files)),
                "summary": record.summary,
                "summary_source": record.summary_source,
            },
        )
        conn.commit()

    def load_session(self, session_id: str) -> Optional[SessionRecord]:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return SessionRecord.from_dict(dict(row))

    def list_sessions(self, limit: int = 50) -> List[SessionRecord]:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY start_time DESC LIMIT ?", (limit,)
        ).fetchall()
        return [SessionRecord.from_dict(dict(r)) for r in rows]

    def get_active_session(self) -> Optional[SessionRecord]:
        """Return the most recent active session, or None."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT * FROM sessions WHERE status = 'active' "
            "ORDER BY start_time DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return SessionRecord.from_dict(dict(row))

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
