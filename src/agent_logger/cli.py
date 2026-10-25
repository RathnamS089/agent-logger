"""CLI entry point for Agent Logger."""

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from .state import AgentLoggerDB, SessionRecord
from .git_tracker import ChangeTracker, get_current_branch, init_repo_if_needed
from .summarizer import generate_summary

load_dotenv()  # Load .env from cwd if present


def _resolve_repo_path(repo_path: str) -> str:
    """Resolve a repo path argument, defaulting to cwd for '.'/empty."""
    if not repo_path or repo_path == ".":
        return os.getcwd()
    return os.path.abspath(os.path.expanduser(repo_path))


def _get_db() -> AgentLoggerDB:
    return AgentLoggerDB()


def cmd_start(args):
    """Start a new tracked coding session."""
    repo_path = _resolve_repo_path(args.repo)
    agent_name = args.agent or os.environ.get("AGENT_LOGGER_AGENT_NAME", "agent")
    if not os.path.isdir(repo_path):
        print(f"ERROR: repository path does not exist: {repo_path}")
        sys.exit(1)

    init_repo_if_needed(repo_path)
    actual_branch = get_current_branch(repo_path)

    session_id = f"session_{uuid.uuid4().hex[:8]}"
    record = SessionRecord(
        session_id=session_id,
        agent_name=agent_name,
        repo_path=repo_path,
        branch=actual_branch,
        status="active",
    )

    db = _get_db()
    db.save_session(record)

    tracker = ChangeTracker(repo_path)
    tracker.snapshot()

    print(f"Started session: {session_id}")
    print(f"Agent: {agent_name}")
    print(f"Repository: {repo_path}")
    print(f"Branch: {actual_branch}")
    print("Track via Discord: /sessions, /status, /changes")
    print("End with: agent-logger end")


def cmd_end(args):
    """End the current active session and persist a change summary."""
    db = _get_db()
    active = db.get_active_session()

    if not active:
        print("No active session found. Use `agent-logger start <repo>` first.")
        return

    s = active
    s.end_time = datetime.now(timezone.utc)
    s.status = "completed"

    # Collect the files touched during this session and persist a summary.
    try:
        tracker = ChangeTracker(s.repo_path)
        result = tracker.compare_since_snapshot()
    except Exception as e:
        print(f"WARNING: could not compare against repo snapshot: {e}")
        result = {
            "all_changed_files": [],
            "new_files": [],
            "summary": {"total_changed": 0, "new_files_count": 0},
        }

    s.files_changed = sorted(result.get("all_changed_files", []))
    s.new_files = sorted(result.get("new_files", []))

    summary = generate_summary(
        activity_count=max(1, len(s.files_changed) + len(s.new_files)),
        file_count=len(s.files_changed) + len(s.new_files),
        changed_files=s.files_changed,
        new_files=s.new_files,
        repo_path=s.repo_path,
        branch=s.branch,
    )
    s.summary = summary
    s.summary_source = "model" if os.environ.get("AGENT_LOGGER_MODEL") else "fallback"
    db.save_session(s)

    duration = s.duration_seconds or 0
    print(f"Ended session: {s.session_id}")
    print(f"Duration: {duration // 60}m {duration % 60}s")
    print(f"Files changed: {len(s.files_changed)}")
    print(f"New files: {len(s.new_files)}")
    print(f"Summary: {summary}")

    if args.cleanup:
        print("Session data persisted.")


def cmd_status(args):
    """Show status of recent sessions."""
    db = _get_db()
    sessions = db.list_sessions(limit=10)

    if not sessions:
        print("No sessions found.")
        return

    print(f"Recent sessions (last {len(sessions)}):")
    for s in sessions:
        end_str = s.end_time.strftime("%Y-%m-%d %H:%M") if s.end_time else "active"
        print(
            f"  {s.session_id[-8:]} | {s.agent_name} | {os.path.basename(s.repo_path)} | "
            f"{s.branch} | {s.status} | started: {s.start_time.strftime('%m-%d %H:%M')} "
            f"ended: {end_str}"
        )
        if s.files_changed:
            print(f"    files changed: {len(s.files_changed)}")
        if s.summary:
            print(f"    summary: {s.summary[:100]}{'...' if len(s.summary) > 100 else ''}")


def cmd_changes(args):
    """Show change summary for the most recent session(s)."""
    db = _get_db()
    sessions = db.list_sessions(limit=max(1, args.limit))

    if not sessions:
        print("No sessions found.")
        return

    for s in sessions:
        changed_files = s.files_changed or []
        new_files = s.new_files or []

        # Use the persisted summary if present, otherwise generate one.
        if s.summary:
            summary = s.summary
            source = s.summary_source or "cached"
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
            source = "generated"

        print(f"Change summary for session {s.session_id[-8:]} [{source}]:")
        print(f"  {summary}")
        print()


def cmd_track(args):
    """Track file changes during current session (polling mode)."""
    repo_path = _resolve_repo_path(args.repo) if args.repo else os.getcwd()

    tracker = ChangeTracker(repo_path)
    print(f"Tracking changes in {repo_path} (branch: {tracker.branch})")
    print("Press Ctrl+C to stop tracking and save snapshot.")

    try:
        import time

        while True:
            result = tracker.compare_since_snapshot()
            print(
                f"\rChanged: {result['summary']['total_changed']} total "
                f"(new since snapshot: {result['summary']['newly_since_snapshot']}) ",
                end="",
                flush=True,
            )
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopping tracking...")
        final = tracker.compare_since_snapshot()
        print("\nFinal summary:")
        print(f"  Total changed: {final['summary']['total_changed']}")
        print(f"  New files: {final['summary']['new_files_count']}")
        print(f"  Files: {', '.join(final['all_changed_files'][:15])}")


def cmd_serve(args):
    """Run the Discord bot (requires AGENT_LOGGER_BOT_TOKEN)."""
    from .discord_bot import run_bot

    token = args.token or os.environ.get("AGENT_LOGGER_BOT_TOKEN")
    if not token:
        print(
            "ERROR: AGENT_LOGGER_BOT_TOKEN not set.\n"
            "Set it in .env or the environment, then run: agent-logger serve"
        )
        sys.exit(1)

    db_path = os.environ.get("AGENT_LOGGER_DB_PATH", "sessions.db")
    allowed = os.environ.get("AGENT_LOGGER_ALLOWED_USER_IDS", "")
    print(f"Starting Discord bot (db: {db_path})")
    run_bot(token, db_path=db_path, allowed_user_ids_str=allowed)


def main():
    parser = argparse.ArgumentParser(
        description="Agent Logger: local coding-agent session logger with Discord bot"
    )
    subparsers = parser.add_subparsers(dest="command")

    # start command
    start_p = subparsers.add_parser("start", help="Start a new tracked coding session")
    start_p.add_argument("repo", nargs="?", default=None, help="Path to the Git repository (default: current dir)")
    start_p.add_argument("--agent", default=None, help="Agent name identifier")
    start_p.add_argument("--branch", default=None, help="Git branch name override")

    # end command
    end_p = subparsers.add_parser("end", help="End the current active session")
    end_p.add_argument("--cleanup", action="store_true", help="Confirm cleanup of tracking data")

    # status command
    subparsers.add_parser("status", help="Show status of recent sessions")

    # changes command
    changes_p = subparsers.add_parser("changes", help="Show change summary for recent session(s)")
    changes_p.add_argument("--limit", type=int, default=1, help="Number of recent sessions to summarize")

    # track command
    track_p = subparsers.add_parser("track", help="Track file changes in real-time")
    track_p.add_argument("repo", nargs="?", default=None, help="Path to the Git repository")

    # serve command
    serve_p = subparsers.add_parser("serve", help="Run the Discord bot")
    serve_p.add_argument("--token", default=None, help="Discord bot token (else AGENT_LOGGER_BOT_TOKEN)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)
    elif args.command == "start":
        cmd_start(args)
    elif args.command == "end":
        cmd_end(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "changes":
        cmd_changes(args)
    elif args.command == "track":
        cmd_track(args)
    elif args.command == "serve":
        cmd_serve(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
