# host-relay

AI agent in a container or a restricted environment? Want to get out? No problem.

`host-relay` is a lightweight bridge that lets an AI agent running inside a
sandboxed environment (Snap, Docker, cloud runner) execute **simple shell
commands** on the host machine via the Model Context Protocol (MCP).

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/page-fault-in-nonpaged-area/host-relay/main/install.sh | bash
```

Or with `uv`:

```bash
uv tool install host-relay
```

## MCP Config

Add to your agent's MCP configuration:

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

## Usage

```bash
hr          # Start the listener (blocks)
hr status   # Check if hr is running
hr stop     # Stop the listener
hr mcp      # Start MCP server (called by agent, not you)
hr --once   # Process one job and exit (for testing)
```

## How It Works

1. The agent calls `host_run("gh repo list")` via MCP
2. The MCP server writes a `.job` file to `~/.host-relay/spool/`
3. The `hr` listener picks it up, validates it, executes it
4. The result is written back as a `.result` file
5. The MCP server reads it and returns it to the agent

No network ports. No root required. Every command is logged to
`~/.host-relay/logs/hr.log`.

## Command Policy

Only simple commands and pipelines are allowed. Rejected:

- Shell programming: `;`, `&&`, `||`, loops, functions
- Command substitution: `` `...` ``, `$(...)`
- Process forks: `nohup`, `disown`, `setsid`, `&`
- Shell interpreters as commands: `bash`, `sh`, `zsh`
- Script execution: `python3 script.py`
- Redirects outside `~` and `/tmp`

## Docs

- [Design](design.md) — architecture and rationale
- [Requirements](requirements.md) — formal EARS requirements
- [Tasks](tasks.md) — implementation task breakdown

