"""Worker — executes a single job and returns a result."""

from __future__ import annotations

import logging
import subprocess
import time

from hr.env_resolver import merge_job_env
from hr.policy import PolicyViolation, validate
from hr.spool import JobFile, ResultFile

logger = logging.getLogger(__name__)


def execute_job(
    job: JobFile,
    host_env: dict[str, str],
    extra_blocked: list[str] | None = None,
) -> ResultFile:
    """Execute a job command and return the result.

    Policy validation is performed first; on violation the command is
    never executed and a result with exit_code=126 is returned.
    """
    # Policy check
    try:
        validate(job.cmd, extra_blocked=extra_blocked)
    except PolicyViolation as exc:
        logger.warning(
            "Policy rejection job=%s cmd=%r rule=%s", job.id, job.cmd, exc.rule
        )
        return ResultFile(
            id=job.id,
            stdout="",
            stderr=exc.message,
            exit_code=126,
        )

    # Build environment
    merged_env = merge_job_env(host_env, job.env if job.env else None)

    # Clamp timeout to prevent DoS from crafted .job files
    effective_timeout = max(1, min(job.timeout, 120))

    # Log job start (env keys only, never values)
    env_keys = sorted(job.env.keys()) if job.env else []
    logger.info(
        "Executing job=%s cmd=%r extra_env_keys=%s timeout=%d",
        job.id, job.cmd, env_keys, effective_timeout,
    )

    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            ["/bin/bash", "-c", job.cmd],
            env=merged_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning("Timeout job=%s after %.1fms, sending SIGTERM", job.id, elapsed)
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning("Job %s did not stop after SIGTERM, sending SIGKILL", job.id)
                proc.kill()
                stdout, stderr = proc.communicate()

            return ResultFile(
                id=job.id,
                stdout=stdout or "",
                stderr=f"Command timed out after {effective_timeout}s",
                exit_code=124,
                elapsed_ms=elapsed,
            )

        elapsed = (time.monotonic() - start) * 1000
        logger.info(
            "Completed job=%s exit_code=%d elapsed_ms=%.1f",
            job.id, proc.returncode, elapsed,
        )
        return ResultFile(
            id=job.id,
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            elapsed_ms=elapsed,
        )

    except FileNotFoundError:
        elapsed = (time.monotonic() - start) * 1000
        return ResultFile(
            id=job.id,
            stdout="",
            stderr="bash: command not found (bash shell not available)",
            exit_code=127,
            elapsed_ms=elapsed,
        )

    except OSError as exc:
        elapsed = (time.monotonic() - start) * 1000
        logger.error("OS error executing job=%s: %s", job.id, exc)
        return ResultFile(
            id=job.id,
            stdout="",
            stderr=str(exc),
            exit_code=127,
            elapsed_ms=elapsed,
        )

