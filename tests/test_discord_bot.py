"""Tests for discord_bot.py — all interactions mocked, no network."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent_logger.discord_bot as discord_bot
from agent_logger.discord_bot import (
    AgentLoggerBot,
    _is_authorized,
    _parse_user_ids,
    _truncate,
)
from agent_logger.state import AgentLoggerDB, SessionRecord

ALLOWED_ID = 111
DENIED_ID = 999


def _interaction(user_id, client):
    """Build a mock discord.Interaction with an AsyncMock response."""
    inter = MagicMock()
    inter.user.id = user_id
    inter.client = client
    inter.response.send_message = AsyncMock()
    return inter


def _make_bot(tmp_path, sessions):
    db = AgentLoggerDB(str(tmp_path / "db.sqlite"))
    for s in sessions:
        db.save_session(s)
    bot = AgentLoggerBot(
        db=db,
        allowed_user_ids={ALLOWED_ID},
        command_prefix="!",
        intents=discord_bot.discord.Intents.default(),
    )
    bot.tree.sync = AsyncMock(return_value=[])  # never hit Discord's API in tests
    _run(bot.setup_hook())  # registers the slash commands
    return bot


def _sent_text(inter):
    call = inter.response.send_message.call_args
    if call is None:
        return ""
    if call.args:
        return str(call.args[0])
    return str(call.kwargs.get("content", ""))


def _run(coro):
    return asyncio.run(coro)


class TestHelpers:
    def test_parse_user_ids(self):
        assert _parse_user_ids("1, 2 ,3") == {1, 2, 3}

    def test_parse_user_ids_empty(self):
        assert _parse_user_ids("") == set()

    def test_truncate_short_passes(self):
        assert _truncate("hi") == "hi"

    def test_truncate_long(self):
        out = _truncate("x" * 3000)
        assert len(out) <= 1900
        assert "truncated" in out


class TestAuth:
    def test_authorized_user_passes(self, tmp_path):
        bot = _make_bot(tmp_path, [])
        assert _is_authorized(_interaction(ALLOWED_ID, bot)) is True

    def test_unauthorized_user_denied(self, tmp_path):
        bot = _make_bot(tmp_path, [])
        assert _is_authorized(_interaction(DENIED_ID, bot)) is False

    def test_empty_allowlist_denied_by_default(self, tmp_path):
        bot = _make_bot(tmp_path, [])
        bot.allowed_user_ids = set()
        with patch.dict(os.environ, {"AGENT_LOGGER_ALLOW_ANY_USER": ""}):
            assert _is_authorized(_interaction(ALLOWED_ID, bot)) is False

    def test_allow_any_user_escape_hatch(self, tmp_path):
        bot = _make_bot(tmp_path, [])
        bot.allowed_user_ids = set()
        with patch.dict(os.environ, {"AGENT_LOGGER_ALLOW_ANY_USER": "1"}):
            assert _is_authorized(_interaction(ALLOWED_ID, bot)) is True

    def test_env_fallback_when_client_attr_missing(self):
        inter = MagicMock()
        inter.user.id = ALLOWED_ID
        inter.client = MagicMock(spec=[])  # lacks allowed_user_ids
        with patch.dict(os.environ, {"AGENT_LOGGER_ALLOWED_USER_IDS": "111,222"}):
            assert _is_authorized(inter) is True


@pytest.fixture
def bot_with_sessions(tmp_path):
    active = SessionRecord("session_abc", "claude", "/repo", "main", "active")
    old = SessionRecord("session_old", "codex", "/repo", "dev", "completed")
    return _make_bot(tmp_path, [active, old])


@pytest.fixture
def empty_bot(tmp_path):
    return _make_bot(tmp_path, [])


class TestSessionsCommand:
    def test_lists_sessions_ephemeral(self, bot_with_sessions):
        cmd = bot_with_sessions.tree.get_command("sessions")
        inter = _interaction(ALLOWED_ID, bot_with_sessions)
        _run(cmd._callback(inter))
        text = _sent_text(inter)
        assert "Recent Sessions" in text
        # Session IDs are displayed as their last 8 chars.
        newest_id = bot_with_sessions.db.list_sessions()[0].session_id[-8:]
        assert newest_id in text
        assert inter.response.send_message.call_args.kwargs.get("ephemeral") is True

    def test_denies_unauthorized(self, bot_with_sessions):
        cmd = bot_with_sessions.tree.get_command("sessions")
        inter = _interaction(DENIED_ID, bot_with_sessions)
        _run(cmd._callback(inter))
        assert "not authorized" in _sent_text(inter).lower()

    def test_empty_db_message(self, empty_bot):
        cmd = empty_bot.tree.get_command("sessions")
        inter = _interaction(ALLOWED_ID, empty_bot)
        _run(cmd._callback(inter))
        assert "No sessions found" in _sent_text(inter)


class TestStatusCommand:
    def test_active_session_shown(self, bot_with_sessions):
        cmd = bot_with_sessions.tree.get_command("status")
        inter = _interaction(ALLOWED_ID, bot_with_sessions)
        _run(cmd._callback(inter))
        text = _sent_text(inter)
        assert "Active Session" in text
        active_id = bot_with_sessions.db.get_active_session().session_id[-8:]
        assert active_id in text
        assert "claude" in text

    def test_no_active_session_message(self, empty_bot):
        cmd = empty_bot.tree.get_command("status")
        inter = _interaction(ALLOWED_ID, empty_bot)
        _run(cmd._callback(inter))
        assert "No active session" in _sent_text(inter)

    def test_completed_only_reports_no_active(self, tmp_path):
        bot = _make_bot(
            tmp_path, [SessionRecord("session_done", "a", "/repo", "main", "completed")]
        )
        cmd = bot.tree.get_command("status")
        inter = _interaction(ALLOWED_ID, bot)
        _run(cmd._callback(inter))
        assert "No active session" in _sent_text(inter)


class TestChangesCommand:
    def test_uses_persisted_summary(self, bot_with_sessions):
        s = bot_with_sessions.db.load_session("session_old")
        s.summary = "Persisted summary text"
        bot_with_sessions.db.save_session(s)
        cmd = bot_with_sessions.tree.get_command("changes")
        inter = _interaction(ALLOWED_ID, bot_with_sessions)
        _run(cmd._callback(inter))
        assert "Persisted summary text" in _sent_text(inter)

    def test_generates_when_no_summary(self, bot_with_sessions):
        cmd = bot_with_sessions.tree.get_command("changes")
        inter = _interaction(ALLOWED_ID, bot_with_sessions)
        with patch.object(discord_bot, "generate_summary", return_value="GENSUM") as m:
            _run(cmd._callback(inter))
        assert "GENSUM" in _sent_text(inter)
        assert m.called

    def test_empty_db_message(self, empty_bot):
        cmd = empty_bot.tree.get_command("changes")
        inter = _interaction(ALLOWED_ID, empty_bot)
        _run(cmd._callback(inter))
        assert "No sessions found" in _sent_text(inter)

    def test_denies_unauthorized(self, bot_with_sessions):
        cmd = bot_with_sessions.tree.get_command("changes")
        inter = _interaction(DENIED_ID, bot_with_sessions)
        _run(cmd._callback(inter))
        assert "not authorized" in _sent_text(inter).lower()


class TestSetupHook:
    def test_setup_hook_registers_commands(self, empty_bot):
        assert empty_bot.tree.get_command("sessions") is not None
        assert empty_bot.tree.get_command("status") is not None
        assert empty_bot.tree.get_command("changes") is not None
        assert empty_bot.tree.sync.await_count == 1  # from _make_bot

    def test_repeated_setup_hook_does_not_duplicate(self, empty_bot):
        _run(empty_bot.setup_hook())
        names = [c.name for c in empty_bot.tree.get_commands()]
        assert sorted(names) == ["changes", "sessions", "status"]
        assert empty_bot.tree.sync.await_count == 2

    def test_setup_hook_sync_failure_is_caught(self, empty_bot):
        empty_bot.tree.sync = AsyncMock(side_effect=RuntimeError("network down"))
        _run(empty_bot.setup_hook())  # must not raise


class TestRunBotConfig:
    def test_run_bot_warns_on_empty_allowlist(self, capsys):
        with patch.dict(
            os.environ,
            {"AGENT_LOGGER_BOT_TOKEN": "", "AGENT_LOGGER_ALLOWED_USER_IDS": ""},
        ):
            with patch.object(
                discord_bot.AgentLoggerBot, "run", side_effect=SystemExit
            ) as mock_run:
                with pytest.raises(SystemExit):
                    discord_bot.run_bot("fake-token", "/tmp/x.db", "")
        captured = capsys.readouterr().out
        assert "WARNING" in captured
        mock_run.assert_called_once()
