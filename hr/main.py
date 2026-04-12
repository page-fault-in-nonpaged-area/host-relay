"""CLI entrypoint for hr."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import time
from pathlib import Path

from hr.config import HR_DIR, LOGS_DIR, PID_FILE, SPOOL_DIR, Config, load_config


logger = logging.getLogger("hr")


# ---------------------------------------------------------------------------
# PID management
# ---------------------------------------------------------------------------

def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we can't signal it


def check_pid() -> None:
    """Exit if another hr instance is already running; clean up stale PID files."""
    if not PID_FILE.exists():
        return

    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return

    if _pid_is_alive(pid):
        print(f"hr is already running (PID {pid})")
        sys.exit(0)
    else:
        PID_FILE.unlink(missing_ok=True)


def write_pid() -> None:
    HR_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def remove_pid() -> None:
    PID_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(config: Config) -> None:
    LOGS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    log_file = LOGS_DIR / "hr.log"

    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))

    root = logging.getLogger()
    root.addHandler(handler)

    # Also log to stderr for interactive use
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root.addHandler(stderr_handler)

    level = logging.DEBUG if os.environ.get("HR_DEBUG") == "1" else logging.INFO
    root.setLevel(level)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_start(config: Config, once: bool = False) -> None:
    """Start the watcher (blocks)."""
    from hr.env_resolver import resolve_host_env
    from hr.watcher import run_watcher

    # Ensure spool dir exists
    SPOOL_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)

    if not once:
        check_pid()
        write_pid()

    print(f"hr starting  pid={os.getpid()}  spool={SPOOL_DIR}  workers={config.worker_count}")
    logger.info("hr starting, pid=%d, spool=%s, workers=%d", os.getpid(), SPOOL_DIR, config.worker_count)

    try:
        host_env = resolve_host_env()
        run_watcher(config, host_env, once=once)
    finally:
        if not once:
            remove_pid()
        logger.info("hr stopped")


def cmd_status() -> None:
    """Print status of the hr process."""
    print(f"  spool : {SPOOL_DIR}")
    print(f"  pid   : {PID_FILE}")
    print(f"  log   : {LOGS_DIR / 'hr.log'}")
    print()

    if not PID_FILE.exists():
        print("Status  : NOT RUNNING (no pid file)")
        _print_spool_info()
        return

    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        print("Status  : NOT RUNNING (invalid pid file)")
        return

    if _pid_is_alive(pid):
        print(f"Status  : RUNNING (PID {pid})")
    else:
        print(f"Status  : NOT RUNNING (stale pid {pid})")
    _print_spool_info()


def _print_spool_info() -> None:
    if not SPOOL_DIR.exists():
        print(f"  spool dir does not exist yet")
        return
    jobs = list(SPOOL_DIR.glob("*.job"))
    results = list(SPOOL_DIR.glob("*.result"))
    print(f"  pending jobs   : {len(jobs)}")
    print(f"  result files   : {len(results)}")
    if results:
        latest = max(results, key=lambda p: p.stat().st_mtime)
        mtime = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(latest.stat().st_mtime))
        print(f"  last result    : {mtime}  ({latest.name})")
    if jobs:
        for j in sorted(jobs)[-3:]:
            age = time.time() - j.stat().st_mtime
            print(f"  pending        : {j.name}  (age {age:.0f}s)")


def cmd_stop() -> None:
    """Stop the running hr process."""
    if not PID_FILE.exists():
        print("hr is not running")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        print("Invalid PID file, removing")
        PID_FILE.unlink(missing_ok=True)
        return

    if not _pid_is_alive(pid):
        print(f"hr is not running (stale PID {pid}), cleaning up")
        PID_FILE.unlink(missing_ok=True)
        return

    print(f"Stopping hr (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    # Wait up to 5s
    for _ in range(50):
        time.sleep(0.1)
        if not _pid_is_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            print("hr stopped")
            return

    print(f"hr (PID {pid}) did not stop within 5 seconds")


def cmd_mcp() -> None:
    """Start the MCP server (stdio transport)."""
    from hr.mcp_server import run_mcp_server
    run_mcp_server()


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hr",
        description="host-relay: Run commands on the host from sandboxed environments",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show hr status")
    sub.add_parser("stop", help="Stop the running hr process")
    sub.add_parser("mcp", help="Start MCP server (stdio transport)")

    parser.add_argument("--once", action="store_true", help="Process one job and exit")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config()

    if args.command == "status":
        cmd_status()
    elif args.command == "stop":
        cmd_stop()
    elif args.command == "mcp":
        cmd_mcp()
    else:
        # Default: start watcher
        setup_logging(config)
        cmd_start(config, once=args.once)


if __name__ == "__main__":
    main()
