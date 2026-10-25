"""Discord bot for Agent Logger with private slash commands.

Provides three private slash commands:
- /sessions: List recent coding sessions
- /status: Show current session status
- /changes: Show summary of changes since last session

All responses are ephemeral (private to the invoking user). Only user IDs
listed in AGENT_LOGGER_ALLOWED_USER_IDS may invoke the commands.

Privacy: full diffs never leave this machine — only file paths, counts, and
short summaries are sent to Discord.
"""

import os
from typing import List, Optional, Set

import discord
from discord import app_commands
from discord.ext import commands

from .state import AgentLoggerDB
from .summarizer import generate_summary

MAX_MESSAGE_LEN = 1900  # Discord hard limit is 2000; leave headroom for formatting


def _parse_user_ids(raw: str) -> Set[int]:
    return {int(x.strip()) for x in (raw or "").split(",") if x.strip()}


def _truncate(text: str, limit: int = MAX_MESSAGE_LEN) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


def _is_authorized(interaction: discord.Interaction) -> bool:
    """Check the invoking user against AGENT_LOGGER_ALLOWED_USER_IDS.

    If the allowlist is empty, access is denied by default (fail closed)
    unless AGENT_LOGGER_ALLOW_ANY_USER=1 is explicitly set.
    """
    allowed: Set[int] = getattr(interaction.client, "allowed_user_ids", set()) or _parse_user_ids(
        os.environ.get("AGENT_LOGGER_ALLOWED_USER_IDS", "")
    )
    if not allowed:
        return os.environ.get("AGENT_LOGGER_ALLOW_ANY_USER", "") == "1"
    return interaction.user.id in allowed


async def _deny(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "You are not authorized to use this command.", ephemeral=True
    )


def _build_commands(bot: commands.Bot) -> List[app_commands.Command]:
    """Register all slash commands on the bot's command tree."""

    commands_list: List[app_commands.Command] = []

    # ── /sessions command ───────────────────────────────────────────────
    @bot.tree.command(name="sessions", description="List recent coding sessions")
    async def sessions_command(interaction: discord.Interaction):
        if not _is_authorized(interaction):
            await _deny(interaction)
            return

        sessions = bot.db.list_sessions(limit=10)
        if not sessions:
            await interaction.response.send_message(
                "No sessions found. Start one with `agent-logger start <repo>`.",
                ephemeral=True,
            )
            return

        lines = [f"**Recent Sessions** ({len(sessions)} found)"]
        for s in sessions[:10]:
            end_str = (
                s.end_time.strftime("%Y-%m-%d %H:%M UTC") if s.end_time else "active"
            )
            lines.append(
                f"• {s.session_id[-8:]} — {s.agent_name} on {s.branch} "
                f"({s.repo_path}) — {s.status} — "
                f"{s.start_time.strftime('%m-%d %H:%M')} → {end_str}"
            )

        await interaction.response.send_message(
            _truncate("\n".join(lines)), ephemeral=True
        )

    commands_list.append(sessions_command)

    # ── /status command ─────────────────────────────────────────────────
    @bot.tree.command(name="status", description="Show current session status")
    async def status_command(interaction: discord.Interaction):
        if not _is_authorized(interaction):
            await _deny(interaction)
            return

        active = bot.db.get_active_session()
        if not active:
            await interaction.response.send_message(
                "No active session. Start one with `agent-logger start <repo>`.",
                ephemeral=True,
            )
            return

        s = active
        seconds = s.duration_seconds or 0
        mins = max(1, seconds // 60)

        await interaction.response.send_message(
            _truncate(
                f"**Active Session**\n"
                f"• Session: {s.session_id[-8:]}\n"
                f"• Agent: {s.agent_name}\n"
                f"• Repository: {s.repo_path} (on {s.branch})\n"
                f"• Status: {s.status}\n"
                f"• Started: {s.start_time.strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"• Duration: ~{mins} min"
            ),
            ephemeral=True,
        )

    commands_list.append(status_command)

    # ── /changes command ────────────────────────────────────────────────
    @bot.tree.command(name="changes", description="Show summary of changes since last session")
    async def changes_command(interaction: discord.Interaction):
        if not _is_authorized(interaction):
            await _deny(interaction)
            return

        sessions = bot.db.list_sessions(limit=1)
        if not sessions:
            await interaction.response.send_message(
                "No sessions found. Start one with `agent-logger start <repo>`.",
                ephemeral=True,
            )
            return

        s = sessions[0]
        changed_files = s.files_changed or []
        new_files = s.new_files or []

        # Prefer the persisted summary; generate only when missing.
        if s.summary:
            summary = s.summary
        else:
            summary = generate_summary(
                activity_count=len(changed_files) + len(new_files),
                file_count=len(changed_files) + len(new_files),
                changed_files=changed_files,
                new_files=new_files,
                repo_path=s.repo_path or "",
                branch=s.branch or "main",
                model_name=os.environ.get("AGENT_LOGGER_MODEL"),
            )

        file_list = ", ".join((changed_files + new_files)[:10]) or "none"
        await interaction.response.send_message(
            _truncate(
                f"**Change Summary** ({s.session_id[-8:]}, {s.status})\n"
                f"{summary}\n"
                f"Files: {file_list}"
            ),
            ephemeral=True,
        )

    commands_list.append(changes_command)

    return commands_list


# ── Bot runner ───────────────────────────────────────────────────────────


class AgentLoggerBot(commands.Bot):
    """Bot subclass that syncs the command tree inside setup_hook."""

    def __init__(self, db: AgentLoggerDB, allowed_user_ids: Set[int], **kwargs):
        super().__init__(**kwargs)
        self.db = db
        self.allowed_user_ids = allowed_user_ids

    async def setup_hook(self):
        # Register commands while the event loop is running, then sync.
        # Clear first so a repeated setup_hook does not raise
        # CommandAlreadyRegistered.
        self.tree.clear_commands(guild=None)
        _build_commands(self)
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} command(s)")
        except Exception as e:
            print(f"Failed to sync commands: {e}")


def run_bot(
    token: str,
    db_path: str = "sessions.db",
    allowed_user_ids_str: str = "",
):
    """Run the Discord bot with the given token and configuration."""
    allowed_ids = _parse_user_ids(allowed_user_ids_str) or _parse_user_ids(
        os.environ.get("AGENT_LOGGER_ALLOWED_USER_IDS", "")
    )
    if not allowed_ids:
        print(
            "WARNING: AGENT_LOGGER_ALLOWED_USER_IDS is empty — all commands "
            "will be denied. Set your Discord user ID in .env."
        )

    bot = AgentLoggerBot(
        db=AgentLoggerDB(db_path),
        allowed_user_ids=allowed_ids,
        command_prefix=os.environ.get("AGENT_LOGGER_PREFIX", "!"),
        intents=discord.Intents.default(),
        activity=discord.Game(name="/sessions | /status | /changes"),
    )
    bot.run(token)


if __name__ == "__main__":
    # Allow running directly: AGENT_LOGGER_BOT_TOKEN=xxx python -m agent_logger.discord_bot
    token = os.environ.get("AGENT_LOGGER_BOT_TOKEN", "")
    if not token:
        print("ERROR: AGENT_LOGGER_BOT_TOKEN environment variable not set")
        print("Set it via: export AGENT_LOGGER_BOT_TOKEN=your-discord-bot-token")
        raise SystemExit(1)
    run_bot(
        token,
        db_path=os.environ.get("AGENT_LOGGER_DB_PATH", "sessions.db"),
        allowed_user_ids_str=os.environ.get("AGENT_LOGGER_ALLOWED_USER_IDS", ""),
    )
