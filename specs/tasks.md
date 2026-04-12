# host-relay — Tasks

Tasks are grouped by phase and ordered by dependency. A phase should be fully complete before the next begins unless noted. Requirement IDs in brackets cross-reference `requirements.md`.

---

## Phase 0 — Project Scaffolding

**T-000** Create the repository layout:
```
host-relay/
  hr/
    __init__.py
    main.py
    watcher.py
    worker.py
    policy.py
    mcp_server.py
    spool.py
    config.py
  tests/
    __init__.py
    test_policy.py
    test_spool.py
    test_worker.py
    test_mcp.py
  install.sh
  pyproject.toml
  design.md
  requirements.md
  tasks.md
  README.md
```

**T-001** Write `pyproject.toml`:
- Package name: `host-relay`
- Entry point: `hr = "hr.main:main"`
- Runtime dependencies: `mcp`, `python-ulid` (or `ulid-py`), no others — keep the dependency footprint minimal and compatible with both Linux and macOS.
- Dev/test dependencies: `pytest`, `pytest-timeout`.
- Minimum Python version: 3.10.
- Include `[tool.uv]` section so `uv tool install host-relay` works.

**T-002** Write `hr/config.py`:
- Define a `Config` dataclass with all tunable values and their defaults: `worker_count=4`, `default_timeout=30`, `max_timeout=120`, `poll_interval_ms=100`, `result_poll_interval_ms=50`, `log_max_bytes=10_485_760`, `log_backup_count=3`, `extra_blocked_executables=[]`.
- Implement `load_config() -> Config` that reads `~/.host-relay/config.json` if present, merges over defaults, and falls back to defaults silently on invalid JSON (log a warning). [REQ-CFG-001, REQ-CFG-002, REQ-CFG-003]

---

## Phase 1 — Spool Module

**T-100** Write `hr/spool.py` — data structures and file I/O helpers:
- Define `JobFile` and `ResultFile` dataclasses matching the JSON schemas in `design.md`.
- Implement `write_job(job: JobFile, spool_dir: Path) -> Path` — writes `<ulid>.job` with mode `0600` using a temp-file + rename pattern.
- Implement `read_job(path: Path) -> JobFile`.
- Implement `write_result(result: ResultFile, spool_dir: Path)` — writes `<ulid>.result.tmp` then renames to `<ulid>.result` atomically. [REQ-SPOOL-006, REQ-HR-016]
- Implement `read_result(path: Path) -> ResultFile`.
- Implement `cleanup_orphans(spool_dir: Path, max_age_seconds: int = 300)` — deletes `.job` and `.result` files older than `max_age_seconds`. [REQ-SPOOL-007]
- Implement `spool_dir_path() -> Path` — returns `~/.host-relay/spool/`, creating it with mode `0700` if absent. [REQ-SPOOL-006]

**T-101** Write `tests/test_spool.py`:
- Test round-trip: write a job, read it back, assert fields match.
- Test atomic result write: result file does not appear until rename completes.
- Test `cleanup_orphans`: files under age limit are kept; files over limit are deleted.
- Test `spool_dir_path()` creates directory with correct permissions.

---

## Phase 2 — Policy Validator

**T-200** Write `hr/policy.py` — static command string validator:
- Define a `PolicyViolation` exception (or dataclass) that carries a `rule` name and a `message`.
- Implement `validate(cmd: str) -> None` — raises `PolicyViolation` on any violation, returns normally if the command is accepted.
- Implement the following checks in order (stop at first violation):
  1. Reject newline characters. [REQ-POL-008]
  2. Reject statement separators (`;`, `&&`, `||`) outside of quoted strings. [REQ-POL-003]
  3. Reject background operator `&` used as a job-control suffix (trailing `&` or `& ` not inside quotes). [REQ-POL-004]
  4. Reject command substitution patterns `` `...` `` and `$(...)`. [REQ-POL-005]
  5. Reject process substitution `<(...)` and `>(...)`. [REQ-POL-006]
  6. Reject subshell/group `(...)` and `{...;}` as compound commands (must distinguish from argument parentheses — e.g. `awk '{print $1}'` is fine). [REQ-POL-007]
  7. Split on unquoted `|` into pipe stages. [REQ-POL-001, REQ-POL-002]
  8. For each stage, extract the executable name (first token, stripping any leading `VAR=val` env prefix assignments).
  9. Reject if executable is in the blocked list. [REQ-POL-009]
  10. Reject if executable is an interpreter binary (`python3`, `ruby`, `node`, `perl`, `php`, `Rscript`, etc.) and the second non-flag argument is a file path (not `-c`). [REQ-POL-010]
  11. Reject `disown`, `nohup`, `setsid`, `screen`, `tmux`, `reptyr`, `start-stop-daemon`, `daemon` anywhere in any stage. [REQ-POL-011]
  12. Parse redirection operators (`>`, `>>`, `<`, `2>`) and reject paths outside `~`, `/tmp`, `/dev/null`. [REQ-POL-012, REQ-POL-013]
- Note: the validator is a **static string analyser**, not a shell. Use regex and careful string parsing; do not invoke a subprocess. Keep it auditable.

**T-201** Write `tests/test_policy.py`:
- One test per rule: each allowed example from `requirements.md §7.2` must pass.
- One test per rule: each rejected example from `requirements.md §7.3` must raise `PolicyViolation` with the correct `rule` name.
- Additional edge cases:
  - Quoted semicolons: `echo "a;b"` → should pass.
  - Quoted backtick: `echo "use \`backtick\`"` → should pass.
  - `awk '{print $1}'` → should pass (braces inside single-quoted argument).
  - `python3 -c "import sys"` → should pass.
  - `python3 script.py` → should fail.
  - Three-stage pipe: `cat f | jq '.' | grep x` → should pass.
  - `cmd1 | cmd2 | bash` → should fail (shell in pipe stage).

---

## Phase 3 — Environment Sourcing

**T-300** Write `hr/env_resolver.py` (or add to `hr/worker.py`):
- Implement `resolve_host_env() -> dict[str, str]`:
  - Detect the user's login shell via `$SHELL` env var, fallback to `bash`.
  - On macOS (detected via `platform.system() == "Darwin"`), prefer `zsh --login -i -c env`; on Linux use `bash --login -i -c env`.
  - Run with a 10-second timeout and `stderr=DEVNULL`.
  - Parse stdout as `KEY=VALUE` lines (handle multi-line values by only splitting on the first `=`).
  - On failure or timeout, log a warning and return `os.environ.copy()`. [REQ-ENV-004]
  - On macOS, additionally parse `/etc/paths` and `/etc/paths.d/*` and prepend their entries to `PATH` if not already present. [REQ-PLAT-009]
- The resolved env is computed once at `hr` startup and stored as a module-level cached value. [REQ-ENV-001]

**T-301** Implement protected-key filtering in the env overlay step:
- When a job's `env` dict is merged, silently drop keys `HOME`, `USER`, `LOGNAME`, `SHELL`, `PATH` and log each dropped key at WARN level. [REQ-ENV-006]

**T-302** Write `tests/test_env_resolver.py`:
- Mock the subprocess call; assert PATH is present in result.
- Assert that on subprocess failure the fallback returns `os.environ` keys.
- Assert that protected keys in a job's `env` dict are dropped.

---

## Phase 4 — Worker

**T-400** Write `hr/worker.py` — `execute_job(job: JobFile, host_env: dict) -> ResultFile`:
- Validate the command via `policy.validate(job.cmd)`. If `PolicyViolation` is raised, return a result with `exit_code=126`, `stderr=violation.message`, `stdout=""`. [REQ-POL-014]
- Build the execution environment: start from `host_env`, overlay `job.env` (with protected-key filtering). [REQ-ENV-005, REQ-ENV-006]
- Run `subprocess.run(["bash", "-c", job.cmd], env=merged_env, capture_output=True, timeout=job.timeout, text=True)`.
- On `subprocess.TimeoutExpired`: send SIGTERM, wait 2 s, SIGKILL if still alive, return timeout result. [REQ-HR-015]
- On `FileNotFoundError` (executable not found): return result with `exit_code=127`. [REQ-ERR-002]
- Return `ResultFile` with stdout, stderr, exit_code, elapsed_ms. [REQ-ERR-001]

**T-401** Write `tests/test_worker.py`:
- Test successful command: `echo hello` → exit 0, stdout contains "hello".
- Test policy rejection: `echo a; echo b` → exit 126, stderr contains rule name.
- Test non-zero exit: `false` → exit 1, no exception raised.
- Test unknown executable: `nonexistent_binary_xyz` → exit 127.
- Test timeout: mock subprocess to hang; assert SIGTERM then SIGKILL sequence and timeout result returned.
- Test env overlay: extra env var accessible in command.
- Test protected key drop: passing `PATH=/evil` in env dict → original PATH used.

---

## Phase 5 — Watcher Loop

**T-500** Write `hr/watcher.py` — `run_watcher(config: Config, host_env: dict)`:
- Create a `ThreadPoolExecutor(max_workers=config.worker_count)`.
- Enter a loop polling `spool_dir_path()` for `.job` files every `config.poll_interval_ms` ms.
- For each `.job` file found:
  - Attempt `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`. Skip file if lock fails. [REQ-HR-010, REQ-HR-011]
  - Check if the job has already exceeded its timeout (compare `job.ts + job.timeout` vs `time.time()`). If so, write a timeout result and delete the job file. [REQ-HR-012]
  - Submit `execute_job(job, host_env)` to the thread pool.
  - In the future callback: call `spool.write_result(result)`, release lock, delete the `.job` file. [REQ-HR-016]
- Every 60 seconds, call `spool.cleanup_orphans()`. [REQ-SPOOL-007]
- On `SIGTERM`/`SIGINT`: set a shutdown flag, call `executor.shutdown(wait=True, cancel_futures=False)`, exit loop. [REQ-HR-006]
- Note: `fcntl` is not available on Windows; wrap in a `try/except ImportError` and use a no-op lock on non-Unix systems (macOS has `fcntl`, so this is only a concern if someone runs `hr` on Windows, which is out of scope but should not crash).

**T-501** Write `tests/test_watcher.py` (integration-level):
- Write a `.job` file to a temp spool directory; start `run_watcher` in a thread; assert a `.result` file appears within 2 seconds.
- Test that a second `hr` instance (simulated by a held flock) does not double-execute the job.
- Test orphan cleanup: manually create old `.job` files; trigger cleanup; assert they are deleted.

---

## Phase 6 — `hr` CLI

**T-600** Write `hr/main.py` — CLI entrypoint using `argparse`:

Subcommands:
- `hr` (no args) — start listener. [REQ-HR-001]
  1. Call `check_pid()` — handle stale PID. [REQ-HR-003]
  2. Write `hr.pid`. [REQ-HR-002]
  3. Resolve host env via `resolve_host_env()`.
  4. Call `run_watcher(config, host_env)` (blocks).
  5. On exit, remove `hr.pid`.
- `hr status` — print running status, pending job count, last result timestamp. [REQ-HR-004]
- `hr stop` — send SIGTERM to PID in `hr.pid`, wait up to 5 s, remove PID file. [REQ-HR-005]
- `hr mcp` — start MCP server (stdio), do not start watcher. [REQ-HR-007]
- `hr --once` — process one job and exit. [REQ-HR-008]

**T-601** Implement `check_pid(pid_path: Path) -> None`:
- If file absent: return.
- Read PID; check `os.kill(pid, 0)` — if process alive, print message and `sys.exit(0)`.
- If `ProcessLookupError`: delete stale file and return. [REQ-HR-003]

**T-602** Write `tests/test_main.py`:
- Test `hr status` when no `hr.pid` exists → prints "not running".
- Test stale PID handling: write a PID file with a dead PID; assert it is cleaned up and `hr` starts.
- Test `hr stop` with no running process → exits cleanly with informative message.

---

## Phase 7 — MCP Server

**T-700** Write `hr/mcp_server.py` using the `mcp` Python SDK (stdio transport):

**Tool: `host_run`** [REQ-MCP-001, REQ-MCP-002, REQ-MCP-003]
- Parameters: `cmd: str`, `env: dict | None`, `timeout: int = 30`.
- Pre-check: if `hr.pid` does not exist or the PID is dead, return MCP error `HR_NOT_RUNNING` with instructional message. [REQ-MCP-006]
- Generate a ULID as `job_id`.
- Write a `.job` file to the spool.
- Poll for `.result` file every `config.result_poll_interval_ms` ms.
- On result found: read, delete both files, return `{ stdout, stderr, exit_code, elapsed_ms }`.
- On poll timeout (`job.timeout + 2s` elapsed): delete `.job` if present, return MCP error `HR_TIMEOUT`. [REQ-SPOOL-005]

**Tool: `spool_status`** [REQ-MCP-004]
- Check `hr.pid` liveness.
- Count `.job` files in spool.
- Find the most recently modified `.result` file; return its mtime as ISO-8601.
- Return structured object.

**Tool: `abort_job`** [REQ-MCP-005]
- Parameter: `job_id: str`.
- Attempt to delete `<job_id>.job`. If absent (job already running or completed), return informational message.

**T-701** Define MCP error codes as constants: `HR_NOT_RUNNING`, `HR_TIMEOUT`, `HR_POLICY_VIOLATION`, `HR_SPOOL_ERROR`. [REQ-ERR-005]

**T-702** Write `tests/test_mcp.py`:
- Mock the spool: pre-write a `.result` file before `host_run` polls → assert result returned correctly.
- Test `HR_NOT_RUNNING` error when `hr.pid` is absent.
- Test `HR_TIMEOUT` when no `.result` file appears within timeout.
- Test `abort_job` deletes the `.job` file; test `abort_job` on non-existent job returns info message.
- Test `spool_status` returns correct pending count.

---

## Phase 8 — Logging

**T-800** Wire `logging.handlers.RotatingFileHandler` into `hr/main.py`:
- Log to `~/.host-relay/logs/hr.log`, max 10 MB, 3 backups. [REQ-LOG-001, REQ-LOG-002]
- Log format: `%(asctime)s %(levelname)s %(name)s %(message)s`.
- Set log level to `INFO` by default; `DEBUG` if `HR_DEBUG=1` is in the environment.

**T-801** Add structured log calls in `worker.py`:
- INFO on job start: `job_id`, `cmd`, subprocess PID.
- INFO on job finish: `job_id`, `exit_code`, `elapsed_ms`.
- WARN on policy rejection: `job_id`, `cmd`, `rule`. [REQ-LOG-003]
- Log only env dict **keys**, never values. [REQ-LOG-005]

**T-802** Add INFO log calls in `main.py` for start, stop, and config load events. [REQ-LOG-004]

---

## Phase 9 — Installer

**T-900** Write `install.sh`:
- Detect OS (`uname -s`): `Linux` or `Darwin`. [REQ-PLAT-001, REQ-PLAT-002]
- Detect available package manager: `uv` → `uv tool install host-relay`; else `pip install --user host-relay`; else print error and exit 1. [REQ-INST-003, REQ-INST-004, REQ-INST-005]
- Create `~/.host-relay/spool/` and `~/.host-relay/logs/` with `mkdir -p -m 700`. [REQ-INST-006]
- Detect shell (`$SHELL`); identify target RC file. [REQ-PLAT-006]
- Check for existing `# host-relay` marker in RC file; append stanza only if absent. [REQ-INST-008]
  ```sh
  # host-relay
  hr status > /dev/null 2>&1 || (hr &)
  ```
- Print MCP config JSON snippet to stdout. [REQ-INST-009]
- Start `hr` in background immediately. [REQ-INST-010]
- All output is plain text, no pager required. [REQ-INST-011]

**T-901** Add fish shell stanza support in the installer:
- Fish stanza invokes `hr` via `command hr` in `~/.config/fish/config.fish`. [REQ-PLAT-007]

**T-902** macOS path extras:
- In `install.sh`, after installing, run `hr` once with `HR_DEBUG=1` to verify the `/etc/paths` sourcing works. Print a diagnostic if `PATH` is suspiciously short.

**T-903** Test the installer manually on:
- Ubuntu (bash)
- macOS (zsh)
- A container with only `sh` and `pip` (no `uv`, no `curl`-based install) — use `python3 install.py` fallback.
- Document results in a `tests/install_matrix.md`.

---

## Phase 10 — Packaging & Distribution

**T-1000** Finalize `pyproject.toml`:
- Add classifiers for Linux and macOS.
- Set `requires-python = ">=3.10"`.
- Add `[project.scripts]` entry point for `hr`.
- Add a `[tool.uv.sources]` stanza for local development.

**T-1001** Add a `Makefile` with targets:
- `make test` — runs `pytest tests/ -v`.
- `make install-dev` — `uv tool install -e .` or `pip install -e .`.
- `make lint` — `ruff check hr/ tests/` (if `ruff` available).

**T-1002** Write `README.md`:
- One-sentence description.
- One-line install command.
- MCP config snippet.
- `hr status`, `hr stop` quick reference.
- Link to `design.md` and `requirements.md`.

---

## Dependency Order Summary

```
T-000, T-001, T-002          (scaffolding — no deps)
  └─► T-100, T-101           (spool)
  └─► T-200, T-201           (policy)
  └─► T-300, T-301, T-302    (env resolver)
        └─► T-400, T-401     (worker — needs policy + env)
              └─► T-500, T-501  (watcher — needs worker)
                    └─► T-600, T-601, T-602  (CLI — needs watcher)
                          └─► T-700, T-701, T-702  (MCP — needs CLI/spool)
                                └─► T-800, T-801, T-802  (logging — cross-cuts, add last)
                                      └─► T-900 … T-903  (installer)
                                            └─► T-1000 … T-1002  (packaging)
```

Phases 0–7 can have their unit tests written in parallel with implementation (TDD encouraged). Phase 9 (installer) should be tested only after Phase 6 (CLI) is stable.
