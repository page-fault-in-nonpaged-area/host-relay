"""MCP server — exposes host_run, spool_status, abort_job tools via stdio."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from hr.config import PID_FILE, SPOOL_DIR, load_config
from hr.spool import JobFile, generate_ulid, read_result, spool_dir_path, write_job

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------
HR_NOT_RUNNING = "HR_NOT_RUNNING"
HR_TIMEOUT = "HR_TIMEOUT"
HR_POLICY_VIOLATION = "HR_POLICY_VIOLATION"
HR_SPOOL_ERROR = "HR_SPOOL_ERROR"

# ---------------------------------------------------------------------------
# MCP app
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "host-relay",
    instructions=(
        "host-relay lets you run simple shell commands on the host machine. "
        "Use 'host_run' to execute commands. The host must have 'hr' running."
    ),
)

config = load_config()


def _hr_is_running() -> bool:
    """Check whether the hr listener process is alive."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError, ProcessLookupError):
        return False


@mcp.tool()
async def host_run(
    cmd: str,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict:
    """Run a simple shell command on the host machine.

    Args:
        cmd: The command to execute (simple commands and pipes only).
        env: Optional extra environment variables as key-value pairs.
        timeout: Execution timeout in seconds (default 30, max 120).

    Returns:
        Dict with stdout, stderr, exit_code, and elapsed_ms.
    """
    if not _hr_is_running():
        return {
            "error": HR_NOT_RUNNING,
            "message": (
                "hr is not running on the host. "
                "Please open a terminal and run 'hr' to start the listener."
            ),
        }

    # Clamp timeout
    timeout = max(1, min(timeout, config.max_timeout))

    spool = spool_dir_path()
    job_id = generate_ulid()
    job = JobFile(id=job_id, cmd=cmd, env=env or {}, timeout=timeout)

    try:
        write_job(job, spool)
    except OSError as exc:
        return {
            "error": HR_SPOOL_ERROR,
            "message": f"Failed to write job file: {exc}",
        }

    # Poll for result
    result_path = spool / f"{job_id}.result"
    job_path = spool / f"{job_id}.job"
    poll_sec = config.result_poll_interval_ms / 1000.0
    deadline = time.monotonic() + timeout + 2  # grace period

    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                result = read_result(result_path)
                # Clean up
                result_path.unlink(missing_ok=True)
                job_path.unlink(missing_ok=True)
                return {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.exit_code,
                    "elapsed_ms": result.elapsed_ms,
                }
            except Exception as exc:
                return {
                    "error": HR_SPOOL_ERROR,
                    "message": f"Failed to read result: {exc}",
                }
        await asyncio.sleep(poll_sec)

    # Timeout — clean up
    job_path.unlink(missing_ok=True)
    return {
        "error": HR_TIMEOUT,
        "message": f"Command timed out after {timeout}s (no result from hr)",
    }


@mcp.tool()
async def spool_status() -> dict:
    """Check the status of the hr listener and spool directory.

    Returns:
        Dict with hr_running, pending_jobs, and last_result_time.
    """
    running = _hr_is_running()
    spool = spool_dir_path()

    pending = len(list(spool.glob("*.job")))

    last_result_time: str | None = None
    results = list(spool.glob("*.result"))
    if results:
        latest = max(results, key=lambda p: p.stat().st_mtime)
        mtime = latest.stat().st_mtime
        last_result_time = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return {
        "hr_running": running,
        "pending_jobs": pending,
        "last_result_time": last_result_time,
    }


@mcp.tool()
async def abort_job(job_id: str) -> dict:
    """Abort a pending job by deleting its job file before hr picks it up.

    Args:
        job_id: The ULID of the job to abort.

    Returns:
        Dict with status message.
    """
    spool = spool_dir_path()
    job_path = spool / f"{job_id}.job"

    if not job_path.exists():
        return {
            "status": "not_found",
            "message": f"Job {job_id} not found — it may already be running or completed.",
        }

    try:
        job_path.unlink()
        return {"status": "aborted", "message": f"Job {job_id} aborted."}
    except OSError as exc:
        return {
            "status": "error",
            "message": f"Failed to abort job {job_id}: {exc}",
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_mcp_server() -> None:
    """Start the MCP server with stdio transport."""
    mcp.run(transport="stdio")
