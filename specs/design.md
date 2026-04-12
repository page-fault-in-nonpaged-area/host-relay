# host-relay — Design Document

## Overview

`host-relay` is a lightweight bridge that lets an AI agent running inside a sandboxed or containerised environment (e.g. a Snap-confined CLI, a Docker container, or a remote cloud runner) execute simple shell commands on the **host machine**, as if it had a real terminal there.

It is composed of two collaborating pieces:

1. **`hr` — the Host Listener** (`hr/`): a Python process that runs on the bare host, watches a shared file-based queue, dispatches commands to a worker pool, sources the user's shell environment, and writes results back.
2. **`host-relay-mcp` — the MCP Server** (`mcp/`): a small Model Context Protocol server the agent loads as a tool. It exposes a `host_run` tool (and companions) that write into the queue and block until the result appears.

The only shared medium between the two sides is a **plain-text spool directory** — a handful of files on the filesystem that both sides can reach. No network ports, no daemons that need root, no complex IPC.

---

## Motivation

Certain AI development environments are AppArmor/seccomp confined (e.g. GitHub Copilot CLI as a Snap package). They can read and write the user's home directory but cannot execute arbitrary host binaries such as `gh`, `docker`, `brew`, etc. The agent needs those tools.

The naive fix — "just install the tool inside the snap" — is not always possible or desirable. A better fix is a thin, auditable relay that:

- Lives entirely in user-space, no `sudo` required after installation.
- Does not open any network socket (avoids firewall/security concerns).
- Is trivially auditable: every command sent and every result returned is a plain text file.
- Enforces a simple command policy so the executor never runs arbitrary shell programs.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Sandboxed Agent (e.g. Copilot CLI snap)             │
│                                                      │
│  ┌──────────────┐   MCP tool call                   │
│  │  Agent LLM   │ ─────────────────► host-relay-mcp │
│  └──────────────┘                        │           │
│                                    writes job file   │
│                                          │           │
└──────────────────────────────────────────┼───────────┘
                                           │  (shared filesystem)
                           ~/.host-relay/spool/
                                           │
┌──────────────────────────────────────────┼───────────┐
│  Host (bare OS)                          │           │
│                                    reads job file    │
│                                          │           │
│                              ┌─────────────────────┐ │
│                              │  hr  (Python)        │ │
│                              │  ┌───────────────┐  │ │
│                              │  │  Watcher loop │  │ │
│                              │  └──────┬────────┘  │ │
│                              │         │            │ │
│                              │  ┌──────▼────────┐  │ │
│                              │  │  Worker pool  │  │ │
│                              │  │  (ThreadPool) │  │ │
│                              │  └──────┬────────┘  │ │
│                              │         │            │ │
│                              │  writes result file  │ │
│                              └─────────────────────┘ │
└───────────────────────────────────────────────────────┘
```

---

## Components in Detail

### 1. Spool Directory — `~/.host-relay/spool/`

The entire communication channel is a directory of small JSON files. No SQLite, no message queue, no network.

```
~/.host-relay/
  spool/
    <job-id>.job     ← written by MCP, consumed by hr
    <job-id>.result  ← written by hr, consumed by MCP
  logs/
    hr.log           ← rotating log of every command + outcome
  hr.pid             ← PID of the running hr process
```

#### Job file schema (`<job-id>.job`)

```json
{
  "id":      "01JREQ7X...",   // ULID, unique per call
  "cmd":     "gh repo list",  // the command string (validated, see below)
  "env":     { "GH_TOKEN": "..." },  // extra env vars (optional)
  "timeout": 30,              // seconds, default 30, max 120
  "ts":      1712345678.123   // unix timestamp written by MCP
}
```

#### Result file schema (`<job-id>.result`)

```json
{
  "id":      "01JREQ7X...",
  "stdout":  "...",
  "stderr":  "...",
  "exit":    0,
  "elapsed": 1.234,
  "ts":      1712345679.357
}
```

The MCP polls for the `.result` file with a short sleep interval (50 ms), with a configurable timeout. Once found, it reads it, deletes both files, and returns the result to the agent.

---

### 2. `hr` — Host Listener

A single Python script (or installed as a `uv`-managed tool). Invoked as:

```bash
hr          # start listening (blocks)
hr --once   # process one job and exit (for testing)
hr status   # print whether the daemon is running
hr stop     # kill the daemon
```

Adding `hr &` or `hr` to a terminal session (or a shell alias) is the intended workflow. The process stays alive in the background. A typical user would wire it into their `~/.bashrc` or `~/.zshrc` using the installer.

#### Worker pool

`hr` runs a `concurrent.futures.ThreadPoolExecutor` with a configurable number of workers (default: 4). Each worker:

1. Picks up a `.job` file from the spool (file-lock via `fcntl` advisory lock to avoid double-pickup).
2. Builds a command environment:
   - Starts from a clean copy of the host's `os.environ`.
   - Sources `~/.bashrc` or `~/.zshrc` (whichever exists) by running a login shell and capturing `env`, then merging.
   - Overlays any extra env vars from the job's `env` dict.
3. Validates the command string against the simple-command policy (see below).
4. Executes via `subprocess.run(["bash", "-c", cmd], ...)` with the merged environment, capturing stdout/stderr.
5. Writes the `.result` file atomically (write to `.result.tmp`, then `os.rename`).

#### Shell RC sourcing

Rather than parsing RC files (which can be arbitrarily complex), the worker uses a subprocess trick:

```python
raw = subprocess.check_output(
    ["bash", "--login", "-i", "-c", "env"],
    env={"HOME": os.environ["HOME"], "TERM": "dumb"},
    stderr=subprocess.DEVNULL
)
host_env = dict(line.split("=", 1) for line in raw.decode().splitlines() if "=" in line)
```

This captures the fully-resolved environment after all RC files have run — PATH, tool-specific tokens, pyenv shims, nvm, etc. — without needing to know which RC files exist or what they do.

---

### 3. `host-relay-mcp` — MCP Server

A lightweight MCP server (Python, using the `mcp` SDK) that the agent connects to. It exposes the following tools:

#### `host_run(cmd, env?, timeout?)`

- **cmd** `string` — the command to run. Subject to the simple-command policy.
- **env** `object` (optional) — extra environment variables to inject, as a flat key/value map. Values may be plain strings or references to the agent's own env (e.g. `{"GH_TOKEN": "$GH_TOKEN"}`).
- **timeout** `integer` (optional, default 30, max 120) — seconds before the relay times out.

Returns `{ stdout, stderr, exit_code, elapsed_ms }`.

#### `spool_status()`

Returns whether `hr` is alive (checks `hr.pid`), the number of pending jobs, and a timestamp of the last completed job.

#### `abort_job(job_id)`

Deletes a pending `.job` file before `hr` picks it up. Has no effect if the job is already running.

---

### 4. Simple-Command Policy

The executor is not a general-purpose shell. The policy ensures nothing dangerous can be passed:

**Allowed:**
- A single command with arguments: `gh repo list --limit 10`
- A single pipe between two commands: `git log --oneline | head -20`
- Redirection to/from files in the user's home directory: `cat ~/.ssh/id_rsa.pub`
- Environment variable prefixes on a command: `GIT_PAGER=cat git diff`

**Rejected (returns an error without executing):**
- Multiple statements: `;`, `&&`, `||`, `&` (background), newlines
- Command substitution: `$(...)`, `` `...` ``
- Forks/spawns/disown: `nohup`, `setsid`, `disown`, `screen`, `tmux`
- Process substitution: `<(...)`, `>(...)` 
- Redirects to paths outside `~` or `/tmp`
- Any use of `eval`, `exec`, `source`, `.` as a command
- Absolute paths to interpreter binaries being used to run scripts: `python3 /some/script.py` is allowed; `bash /some/script.sh` is not

The policy is implemented as a small parser/validator that runs **before** any subprocess is spawned. It is not a shell; it is a static string analyser. This makes it auditable and testable without execution.

---

## Installation

The goal is a single command the user runs once in their real terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/page-fault-in-nonpaged-area/host-relay/main/install.sh | bash
```

Or, if `uv` is already present:

```bash
uvx host-relay install
```

The installer:

1. Installs `hr` as a `uv` tool (or falls back to `pip install --user`).
2. Creates `~/.host-relay/spool/` and `~/.host-relay/logs/`.
3. Appends a one-liner to `~/.bashrc` (and `~/.zshrc` if present) that starts `hr` in the background on new shells, idempotently (checks `hr.pid` first).
4. Prints the MCP config snippet the user pastes into their agent config (e.g. Claude Desktop's `claude_desktop_config.json`, VS Code MCP settings, or Copilot CLI's MCP config).
5. Starts `hr` immediately in the current shell.

The MCP server config snippet looks like:

```json
{
  "mcpServers": {
    "host-relay": {
      "command": "hr",
      "args": ["mcp"]
    }
  }
}
```

There is no separate MCP server process to manage. `hr mcp` sub-command starts the MCP server mode; the agent's MCP client launches it on demand (standard MCP stdio transport).

---

## Security Considerations

- **Local only.** The spool directory is `~/.host-relay/spool/` with mode `0700`. No network socket is opened at any point.
- **No privilege escalation.** `hr` runs as the invoking user. It cannot do anything that user could not already do in a terminal.
- **Simple-command policy.** Prevents the agent from accidentally (or maliciously) running complex shell programs or exfiltrating data via command substitution chains.
- **Timeout enforcement.** All subprocesses are killed after `timeout` seconds via `subprocess.run(..., timeout=...)`.
- **Log retention.** `~/.host-relay/logs/hr.log` keeps a rolling record of every command run, who requested it (MCP client PID), and the exit code. Max 10 MB, 3 rotations.
- **Env var injection safety.** Extra env vars from the agent are overlaid last — they cannot override `PATH` or `HOME` (those are protected keys).

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| IPC mechanism | Files in `~/.host-relay/spool/` | Zero dependencies, fully auditable, works across snap/container boundaries that share the host home dir |
| Language | Python (uv tool) | Ships as a single installable; uv gives hermetic deps without polluting system Python |
| MCP transport | stdio (launched by agent) | Standard MCP pattern; no port to manage; agent client owns the lifecycle |
| Shell sourcing | `bash --login -i -c env` subprocess | Captures the real resolved environment without parsing RC files |
| Concurrency | `ThreadPoolExecutor` | Commands are I/O-bound; threads are simpler than asyncio for subprocess management |
| Command validation | Static string analyser | Fast, testable, no execution needed, easy to audit |

---

## What This Is Not

- It is **not** a general remote execution framework. Use SSH for that.
- It is **not** designed for high throughput. It is for developer tooling (a few commands per minute at most).
- It is **not** a security boundary. It runs as the user. The simple-command policy is a usability guardrail, not a security sandbox.

---

## Repository Layout (planned)

```
host-relay/
  design.md          ← this file
  requirements.md    ← functional & non-functional requirements
  tasks.md           ← implementation task breakdown
  hr/
    __init__.py
    main.py          ← CLI entrypoint (hr, hr mcp, hr status, hr stop)
    watcher.py       ← spool directory watcher loop
    worker.py        ← worker: env sourcing, validation, execution
    policy.py        ← simple-command policy validator
    mcp_server.py    ← MCP server (stdio transport)
    spool.py         ← job/result file read/write helpers
    config.py        ← config (spool path, pool size, timeouts)
  install.sh         ← one-line installer
  pyproject.toml     ← uv/pip packaging
  tests/
    test_policy.py
    test_worker.py
    test_spool.py
    test_mcp.py
```
