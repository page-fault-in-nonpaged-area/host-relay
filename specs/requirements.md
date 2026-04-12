# host-relay — Requirements

EARS notation is used throughout. Patterns used:
- **Ubiquitous**: The `<system>` shall `<response>`
- **Event-driven**: When `<trigger>`, the `<system>` shall `<response>`
- **Condition-driven**: If `<condition>`, the `<system>` shall `<response>`
- **State-driven**: While `<state>`, the `<system>` shall `<response>`
- **Compound**: combinations of the above

Systems referenced:
- **The installer** — `install.sh` / `uvx host-relay install`
- **hr** — the host listener process
- **The MCP server** — the `hr mcp` stdio MCP server
- **The policy validator** — the static command string analyser
- **The worker** — a single thread in hr's thread pool executing one job

---

## 1. Installation

### 1.1 One-line install

**REQ-INST-001** The installer shall be invocable by a single command that requires no arguments and no prior configuration.

**REQ-INST-002** The installer shall support execution via `curl -fsSL <url> | bash` on both Linux and macOS without modification.

**REQ-INST-003** Where `uv` is present on the host, the installer shall install `hr` as a `uv` tool using `uv tool install host-relay`.

**REQ-INST-004** Where `uv` is absent and `pip` is present, the installer shall fall back to `pip install --user host-relay`.

**REQ-INST-005** Where neither `uv` nor `pip` is present, the installer shall print a clear diagnostic message and exit with a non-zero code without making any changes to the system.

**REQ-INST-006** The installer shall create `~/host-relay/` and `~/.host-relay/logs/` with permissions `0700`.

**REQ-INST-007** The installer shall append a startup stanza to `~/.bashrc` on Linux, `~/.zshrc` on macOS (if present), and `~/.bash_profile` on macOS if `~/.bashrc` is absent, such that `hr` is started idempotently in new interactive shells.

**REQ-INST-008** The startup stanza shall be idempotent. When `<installer is run more than once>`, the installer shall not append duplicate stanzas.

**REQ-INST-009** The installer shall print to stdout an MCP configuration snippet ready to paste into the agent's MCP config file (e.g. `claude_desktop_config.json`, VS Code MCP settings).

**REQ-INST-010** The installer shall start `hr` in the background immediately in the current shell session after installation completes.

**REQ-INST-011** The installer shall produce no output that requires a pager. All installer output shall fit in a standard 80-column terminal without line wrapping logic.

---

## 2. Platform Compatibility

### 2.1 Supported platforms

**REQ-PLAT-001** The system shall support Linux x86_64 and ARM64 as both the sandboxed-agent side and the host side.

**REQ-PLAT-002** The system shall support macOS (Apple Silicon and Intel) as the host side. The sandboxed-agent side on macOS is out of scope but must not be actively broken.

**REQ-PLAT-003** The system shall not assume a specific Linux distribution. It shall not depend on packages that are absent from a minimal Ubuntu, Debian, Fedora, or Alpine image.

**REQ-PLAT-004** `hr` shall run on Python 3.10 or later on both Linux and macOS.

**REQ-PLAT-005** `hr` shall not depend on any OS-level system service (systemd, launchd, DBus, etc.) for its core operation.

### 2.2 Shell compatibility

**REQ-PLAT-006** The installer shall detect the user's default shell (`$SHELL`) and append the startup stanza to the appropriate RC file: `~/.bashrc` for bash, `~/.zshrc` for zsh, `~/.config/fish/config.fish` for fish.

**REQ-PLAT-007** Where the detected shell is fish, the installer shall use a fish-compatible stanza that invokes `hr` via `bash` or `sh` since `hr` is a Python-based CLI, not a fish function.

**REQ-PLAT-008** The host environment sourcing step within `hr` shall attempt to source `~/.bashrc`, then `~/.bash_profile`, then `~/.zshrc` (in that order, first one found wins) using a login shell subprocess, regardless of which shell the user's default shell is, because the goal is to capture the fully-resolved PATH and tool environment.

**REQ-PLAT-009** On macOS, the installer shall additionally check for and source `/etc/paths` and `/etc/paths.d/*` entries when building the host PATH, as macOS does not source these in non-login subshells.

---

## 3. `hr` — Host Listener

### 3.1 Lifecycle

**REQ-HR-001** The system shall provide a command `hr` that, when invoked with no arguments, starts the host listener and blocks until interrupted.

**REQ-HR-002** When started, `hr` shall write its PID to `~/.host-relay/hr.pid`.

**REQ-HR-003** When `hr` is started and a `hr.pid` file already exists, `hr` shall check whether the recorded PID corresponds to a running process. If the process is running, `hr` shall print a message and exit. If the process is not running (stale PID), `hr` shall remove the stale file and start normally.

**REQ-HR-004** The system shall provide `hr status`, which prints whether a `hr` process is currently running, the number of jobs pending in the spool, and the timestamp of the last completed job.

**REQ-HR-005** The system shall provide `hr stop`, which sends SIGTERM to the running `hr` process, waits up to 5 seconds for it to exit, and removes `hr.pid`.

**REQ-HR-006** When `hr` receives SIGTERM or SIGINT, it shall finish any currently-executing jobs, write their results, and exit cleanly before the timeout elapses.

**REQ-HR-007** The system shall provide `hr mcp`, which starts the MCP server in stdio transport mode and does not start the watcher loop. The agent's MCP client is responsible for launching this process.

**REQ-HR-008** The system shall provide `hr --once`, which processes the next available job (or waits up to 10 seconds for one), then exits. Intended for testing and CI.

### 3.2 Watcher loop

**REQ-HR-009** While running, `hr` shall poll `~/host-relay/` for new `.job` files at an interval not exceeding 100 milliseconds.

**REQ-HR-010** When a new `.job` file is detected, `hr` shall acquire an advisory file lock on it before dispatching it to a worker, to prevent double-pickup when multiple `hr` instances are accidentally running.

**REQ-HR-011** If the advisory lock on a `.job` file cannot be acquired, `hr` shall skip that file in the current poll cycle and retry on the next cycle.

**REQ-HR-012** When a `.job` file has been present in the spool for longer than its stated `timeout` value without being picked up, `hr` shall write a timeout result file and delete the job file.

### 3.3 Worker pool

**REQ-HR-013** `hr` shall maintain a `ThreadPoolExecutor` with a configurable worker count (default: 4, minimum: 1, maximum: 32) read from `~/.host-relay/config.json`.

**REQ-HR-014** Each worker shall execute its assigned command in a subprocess and not block other workers while doing so.

**REQ-HR-015** When a worker's subprocess exceeds the job's `timeout` value, the worker shall send SIGTERM to the subprocess, wait 2 seconds, then send SIGKILL if the process has not exited, and write a timeout error result.

**REQ-HR-016** The worker shall write the result file atomically: first writing to `<job-id>.result.tmp`, then using `os.rename()` to move it into place, to prevent the MCP server from reading a partial result.

---

## 4. Environment Sourcing

**REQ-ENV-001** Each worker shall resolve the host shell environment once per `hr` startup and cache it for the lifetime of the process, rather than re-sourcing RC files on every job.

**REQ-ENV-002** The system shall resolve the host shell environment by spawning `bash --login -i -c env` (or `zsh --login -i -c env` on macOS where `zsh` is the default shell) and parsing the output as `KEY=VALUE` lines.

**REQ-ENV-003** When sourcing the host environment, `hr` shall suppress stderr output from the RC files to avoid polluting the log.

**REQ-ENV-004** If the environment sourcing subprocess fails or times out (within 10 seconds), `hr` shall fall back to `os.environ` and log a warning. `hr` shall not refuse to start.

**REQ-ENV-005** When a job includes an `env` dict, the worker shall overlay those key-value pairs on top of the resolved host environment before spawning the command subprocess.

**REQ-ENV-006** The worker shall refuse to override the keys `HOME`, `USER`, `LOGNAME`, `SHELL`, and `PATH` via the job's `env` dict. If any of these keys are present in the job's `env` dict, `hr` shall log a warning and silently drop those keys.

---

## 5. Spool File Protocol

**REQ-SPOOL-001** Job files shall be named `<ulid>.job` and result files `<ulid>.result`, where the ULID is generated by the MCP server at job creation time and is the same for both files.

**REQ-SPOOL-002** Job and result files shall be JSON-encoded UTF-8 text files.

**REQ-SPOOL-003** The MCP server shall poll for its result file at an interval not exceeding 50 milliseconds.

**REQ-SPOOL-004** When the MCP server reads a result file successfully, it shall delete both the `.result` file and any remaining `.job` file before returning the result to the caller.

**REQ-SPOOL-005** If the MCP server's overall wait exceeds the job's `timeout` plus a 2-second grace period, the MCP server shall return a timeout error to the caller and delete the `.job` file if it still exists.

**REQ-SPOOL-006** The spool directory shall be created with permissions `0700`. Job and result files shall be created with permissions `0600`.

**REQ-SPOOL-007** `hr` shall periodically (every 60 seconds) scan the spool directory and delete any orphaned files (`.job` or `.result` files older than 5 minutes) to prevent accumulation.

---

## 6. MCP Server

**REQ-MCP-001** The MCP server shall implement the Model Context Protocol using stdio transport.

**REQ-MCP-002** The MCP server shall expose a tool named `host_run` with the following parameters:
- `cmd` (string, required): the command string to execute.
- `env` (object, optional): key-value pairs of extra environment variables.
- `timeout` (integer, optional, default 30, max 120): execution timeout in seconds.

**REQ-MCP-003** The `host_run` tool shall return a structured result containing `stdout`, `stderr`, `exit_code`, and `elapsed_ms`.

**REQ-MCP-004** The MCP server shall expose a tool named `spool_status` that returns: whether `hr` is running, the count of `.job` files pending, and the ISO-8601 timestamp of the most recently modified `.result` file, if any.

**REQ-MCP-005** The MCP server shall expose a tool named `abort_job` with parameter `job_id` (string) that deletes the corresponding `.job` file if it has not yet been picked up. If the job is already running, `abort_job` shall return an informational message stating the job cannot be aborted.

**REQ-MCP-006** When `host_run` is called and `hr` is not running (no live `hr.pid`), the MCP server shall return an actionable error message instructing the user to run `hr` in a terminal, rather than silently timing out.

**REQ-MCP-007** The MCP server shall not require network access. It shall communicate exclusively through the spool directory.

---

## 7. Command Policy

### 7.1 Structure

**REQ-POL-001** The policy validator shall accept a command string consisting of one or more pipe-separated stages, where each stage is a single executable invocation with arguments.

**REQ-POL-002** The policy validator shall accept multiple pipe stages (i.e. `cmd1 | cmd2 | cmd3 | ...`) provided each stage satisfies the per-stage rules below.

**REQ-POL-003** The policy validator shall reject any command string containing shell statement separators: `;`, `&&`, `||`.

**REQ-POL-004** The policy validator shall reject any command string containing background execution operators: `&` used as a job-control suffix (e.g. `cmd &`). A literal `&` inside a quoted argument is permitted.

**REQ-POL-005** The policy validator shall reject any command string containing command substitution: `` `...` `` or `$(...)`.

**REQ-POL-006** The policy validator shall reject any command string containing process substitution: `<(...)` or `>(...)`.

**REQ-POL-007** The policy validator shall reject any command string containing subshell grouping: `(...)` or `{...}` used as compound commands (not as literal arguments).

**REQ-POL-008** The policy validator shall reject any command string containing newline characters (`\n`, `\r`).

**REQ-POL-009** The policy validator shall reject any command string where the executable in any pipe stage is one of the following: `bash`, `sh`, `zsh`, `fish`, `ksh`, `csh`, `tcsh`, `dash`, `eval`, `exec`, `source`, `.`, `nohup`, `disown`, `setsid`, `screen`, `tmux`, `reptyr`.

**REQ-POL-010** The policy validator shall reject any command string where a pipe stage invokes an interpreter to run a script file, identified as: a Python/Ruby/Node/Perl/etc. interpreter binary followed directly by a file path argument (e.g. `python3 /path/to/script.py`). Invoking an interpreter with `-c` and an inline expression (e.g. `python3 -c "print(1)"`) is permitted.

**REQ-POL-011** The policy validator shall reject any command string that attempts to spawn detached or disowned subprocesses. This includes but is not limited to: `nohup cmd`, `cmd &`, `disown`, `setsid cmd`, `start-stop-daemon`, `daemon`.

**REQ-POL-012** The policy validator shall reject any command string that uses output redirection to a path outside of `~` (the user's home directory) and `/tmp`. Redirection to `/dev/null` is permitted.

**REQ-POL-013** The policy validator shall reject any command string that uses input redirection from a path outside of `~` and `/tmp`.

**REQ-POL-014** If `<a command string is rejected by the policy validator>`, the system shall return an error result to the MCP server immediately, without spawning any subprocess, and shall include a human-readable explanation of which rule was violated.

### 7.2 Allowed examples (non-exhaustive)

- `gh repo list --limit 10`
- `git log --oneline | head -20`
- `cat some.json | jq '.' | grep "some_val"`
- `ls -la ~/Projects | sort -k5 -rn | head -10`
- `GH_TOKEN=abc123 gh api /user`
- `docker ps --format '{{.Names}}' | grep web`
- `python3 -c "import sys; print(sys.version)"`
- `cat ~/.ssh/id_rsa.pub`

### 7.3 Rejected examples (non-exhaustive)

- `gh repo list; rm -rf ~`  (statement separator)
- `$(whoami)` (command substitution)
- `bash -c "rm -rf /"` (shell interpreter as command)
- `python3 /home/user/evil.py` (interpreter + script file)
- `nohup long-job &` (detach/disown pattern)
- `cmd1 | cmd2 | bash` (shell interpreter in pipe)
- `cat /etc/passwd | tee ~/out.txt > /etc/hosts` (redirect outside home/tmp)
- `while true; do curl x; done` (loop / compound command)

---

## 8. Logging

**REQ-LOG-001** `hr` shall write structured log entries to `~/.host-relay/logs/hr.log` for every job received, including: job ID, command string, PID of spawned subprocess, start timestamp, finish timestamp, exit code.

**REQ-LOG-002** `hr` shall rotate log files when the log file exceeds 10 MB, keeping at most 3 rotated files.

**REQ-LOG-003** `hr` shall log policy rejections at WARN level, including the rejected command string and the violated rule name.

**REQ-LOG-004** `hr` shall log at INFO level when it starts, when it stops, and when its worker count is changed.

**REQ-LOG-005** Log entries shall not include the values of `env` variables supplied by the job, as these may contain secrets. They shall only log the keys.

---

## 9. Configuration

**REQ-CFG-001** `hr` shall read configuration from `~/.host-relay/config.json` if the file exists, and apply built-in defaults if it does not.

**REQ-CFG-002** The following values shall be configurable: worker pool size, default job timeout, maximum allowed job timeout, spool poll interval, log rotation size, log rotation count, and the list of blocked executable names in the policy (extending, not replacing, the built-in list).

**REQ-CFG-003** When `<~/.host-relay/config.json> is present but contains invalid JSON`, `hr` shall log an error, use built-in defaults, and continue running rather than refusing to start.

---

## 10. Error Handling

**REQ-ERR-001** When a worker subprocess exits with a non-zero exit code, `hr` shall write a result file with the captured stdout, stderr, and the exit code. It shall not treat a non-zero exit code as an `hr`-level error.

**REQ-ERR-002** When a worker subprocess cannot be started (e.g. the executable is not found), `hr` shall write a result file with an empty stdout, the OS error message in stderr, and exit code `127`.

**REQ-ERR-003** When writing a result file fails (e.g. disk full), `hr` shall log the failure at ERROR level and attempt to write a minimal error result. If that also fails, `hr` shall log and continue running; it shall not crash.

**REQ-ERR-004** When the spool directory is not accessible at startup, `hr` shall print a human-readable error and exit with code `1`.

**REQ-ERR-005** The MCP server shall surface `hr`-level errors (timeout, policy rejection, `hr` not running) as MCP tool errors with distinct, machine-readable error codes so the agent can reason about them without parsing free-text.
