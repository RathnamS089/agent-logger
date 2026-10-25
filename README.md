# Agent Logger

A local coding-agent session logger with a private Discord bot. Track coding sessions,
record file changes, and get AI-powered summaries — all running locally.

## Core Features

- **Session tracking**: Start/end coding sessions with agent name, repo, branch, and status
- **Change tracking**: Record uncommitted changes and newly created files during a session
- **Local summarization**: Open-weight model (via Ollama or llama-cpp) generates concise session summaries;
  deterministic fallback works offline
- **Private Discord bot**: `/sessions`, `/status`, and `/changes` commands restricted to your user ID
- **Private by design**: Full diffs never leave the local environment; summaries and file paths only

## Quick Start

### 1. Install dependencies

```bash
# Clone/create the repository
git clone <your-repo> agent-logger
cd agent-logger

# Create a virtual environment (already set up)
source .venv/bin/activate

# Install the package in development mode
pip install -e .

# Or using the venv python directly:
/Users/rathnam.s/agent-logger/.venv/bin/python3 -m pip install -e .
```

### 2. Set up environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:
- `AGENT_LOGGER_BOT_TOKEN` — Your Discord bot token (required for Discord commands)
- `AGENT_LOGGER_ALLOWED_USER_IDS` — Your Discord user ID (for private commands)
- `AGENT_LOGGER_MODEL` — Set to `ollama:phi` to use Ollama's phi model, or leave empty for fallback only
- `AGENT_LOGGER_DB_PATH` — Optional: path for the SQLite session database

### 3. Ensure Ollama is running (for model-powered summaries)

```bash
# Start Ollama server
ollama serve

# Pull the phi model (or your preferred model)
ollama pull phi
```

### 4. Start a tracking session

```bash
# Start a new session in the current repo
agent-logger start .

# Or specify a different path and agent name
agent-logger start /path/to/your/repo --agent "my-coding-agent"
```

This will:
- Initialize a git repo if needed
- Record the current branch
- Create a session entry in the SQLite database
- Take an initial snapshot of file changes

### 5. Make changes to your codebase

While the session is active, make any changes you want:
- Uncommitted modifications
- New files created
- The tracker records everything

### 6. End the session

```bash
agent-logger end
```

This will:
- Mark the session as completed
- Record the end time and duration
- Save all tracked file changes

### 7. Query via Discord (or CLI)

**Via Discord** — start the bot first (token required):

```bash
agent-logger serve
# or: python -m agent_logger.discord_bot
```

Then in Discord:

```
/sessions    → List recent sessions
/status      → Show current active session status
/changes     → Get concise summary of the most recent session
```

Commands respond ephemerally (visible only to you) and are restricted to the
user IDs in `AGENT_LOGGER_ALLOWED_USER_IDS`.

**Via CLI**:

```bash
# Show recent sessions
agent-logger status

# Show change summary for the most recent session
agent-logger changes

# Show change summary for a specific session
agent-logger changes --limit 1
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Agent Logger MVP                           │
├─────────────────────┬─────────────────────┬─────────────────────┤
│  CLI (agent-logger) │  SQLite DB (sessions)│  Git Tracker        │
│  agent-logger start│  sessions.db        │  change detection   │
│  agent-logger end  │  + session records  │  + diff generation  │
└─────────────────────┴─────────────────────┴─────────────────────┘
           │                       │                       │
           ▼                       ▼                       ▼
  /sessions  /status  /changes  summaries  file tracking  local model
  (Discord)   (Discord)   (Discord)  via Ollama   via git diff   (phi/fallback)
```

## Configuration

| Setting | Description | Default |
|---|---|---|
| `AGENT_LOGGER_BOT_TOKEN` | Discord bot token | *(required for `serve`)* |
| `AGENT_LOGGER_ALLOWED_USER_IDS` | Comma-separated Discord user IDs allowed to use commands (empty = deny all) | *(empty)* |
| `AGENT_LOGGER_ALLOW_ANY_USER` | Set to `1` to allow any user when the allowlist is empty (not recommended) | *(unset)* |
| `AGENT_LOGGER_MODEL` | Model for summaries: `ollama:phi` for Ollama, empty for deterministic fallback | *(empty = fallback)* |
| `AGENT_LOGGER_DB_PATH` | SQLite database path for sessions | `sessions.db` |
| `OLLAMA_HOST` | Ollama server URL | `http://127.0.0.1:11434` |
| `AGENT_LOGGER_LLM_CPP_MODEL` | Path to a .gguf file for llama-cpp-python | *(unset)* |

## Development & Testing

### Project structure

```
agent-logger/
├── pyproject.toml          # Package config, entry point: agent_logger.cli:main
├── uv.lock                # Pinned dependencies
├── .python-version        # Python 3.14
├── .venv/                 # Virtual environment
├── src/agent_logger/      # Source package
│   ├── __init__.py        # Package init, __version__ = "0.1.0"
│   ├── __main__.py        # python -m agent_logger support
│   ├── cli.py            # CLI entry point (start/end/status/changes/track/serve)
│   ├── state.py          # SQLite session persistence (AgentLoggerDB, SessionRecord)
│   ├── git_tracker.py    # Git diff & change tracking (ChangeTracker, get_diff_summary)
│   ├── summarizer.py     # Local model summarization (generate_summary, _fallback_summary)
│   └── discord_bot.py    # Discord bot with /sessions /status /changes slash commands
├── tests/                 # Pytest suite (92 tests: state, git, summarizer, bot, CLI)
├── scripts/demo.sh        # Reproducible end-to-end demo
├── .env.example          # Environment variable template
└── sessions.db            # Created at runtime (SQLite)
```

### Running tests

```bash
# Run from the agent-logger directory
.venv/bin/python3 -m pytest

# Reproducible demo (creates a throwaway repo, runs a full session, cleans up)
bash scripts/demo.sh
```

### Verifying the installation

```bash
agent-logger --help
# Should show: start, end, status, changes, track, serve
```

## Honest Assessment

This MVP works entirely offline for the local tracking and summarization components:

- ✅ Session tracking with SQLite — works offline
- ✅ Git change detection — works offline
- ✅ Deterministic summary fallback — works entirely offline, no model needed
- ✅ Discord bot command handlers — require Discord bot token and internet connection
- ✅ Secret redaction — full diffs never sent to model or Discord; only file paths and summaries
- ✅ Private responses — all Discord replies are ephemeral (visible only to the commanding user)

The open-weight model component (`AGENT_LOGGER_MODEL=phi` with Ollama) is essential to the product
but optional — the deterministic fallback produces usable summaries when the model is unavailable
or offline.

## Credits

Built with Hermes Agent, Python 3.14, and local open-weight models. Privacy-first design ensures
your code and session data never leave your control.