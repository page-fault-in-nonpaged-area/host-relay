"""Watcher loop — polls spool directory and dispatches jobs to the worker pool."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path

from hr.config import Config
from hr.spool import (
    JobFile,
    ResultFile,
    cleanup_orphans,
    read_job,
    spool_dir_path,
    write_result,
)
from hr.worker import execute_job

logger = logging.getLogger(__name__)

# File locking — fcntl on Unix, no-op fallback otherwise
try:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

except ImportError:  # pragma: no cover
    def _try_lock(fd: int) -> bool:
        return True

    def _unlock(fd: int) -> None:
        pass


_shutdown = threading.Event()


def _signal_handler(signum: int, _frame: object) -> None:
    logger.info("Received signal %d, shutting down...", signum)
    _shutdown.set()


def run_watcher(
    config: Config,
    host_env: dict[str, str],
    spool_dir: Path | None = None,
    once: bool = False,
) -> None:
    """Main watcher loop. Blocks until SIGTERM/SIGINT or *once* flag."""
    _shutdown.clear()

    # Install signal handlers (main thread only)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

    spool = spool_dir_path(spool_dir)
    pool = ThreadPoolExecutor(max_workers=config.worker_count)
    poll_sec = config.poll_interval_ms / 1000.0
    last_cleanup = time.monotonic()

    # Track open file descriptors for locked jobs
    locked_fds: dict[str, int] = {}
    pending_futures: dict[str, Future] = {}

    logger.info("Watcher started, workers=%d, spool=%s", config.worker_count, spool)

    try:
        while not _shutdown.is_set():
            _poll_once(spool, pool, host_env, config, locked_fds, pending_futures)

            # Periodic orphan cleanup (every 60s)
            now = time.monotonic()
            if now - last_cleanup > 60:
                removed = cleanup_orphans(spool)
                if removed:
                    logger.info("Cleaned up %d orphaned spool files", removed)
                last_cleanup = now

            if once:
                # In --once mode, process one job then exit
                # Wait a bit for the result to be written
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if not pending_futures:
                        break
                    time.sleep(0.1)
                break

            _shutdown.wait(timeout=poll_sec)

    finally:
        logger.info("Shutting down watcher, waiting for in-flight jobs...")
        pool.shutdown(wait=True, cancel_futures=False)
        # Release all held locks
        for fd in locked_fds.values():
            _unlock(fd)
            try:
                os.close(fd)
            except OSError:
                pass
        logger.info("Watcher stopped")


def _poll_once(
    spool: Path,
    pool: ThreadPoolExecutor,
    host_env: dict[str, str],
    config: Config,
    locked_fds: dict[str, int],
    pending_futures: dict[str, Future],
) -> None:
    """Poll for new .job files and dispatch them."""
    # Clean up completed futures
    done_ids = [jid for jid, fut in pending_futures.items() if fut.done()]
    for jid in done_ids:
        fut = pending_futures.pop(jid)
        # Release lock
        fd = locked_fds.pop(jid, None)
        if fd is not None:
            _unlock(fd)
            try:
                os.close(fd)
            except OSError:
                pass
        # Check for exceptions
        exc = fut.exception()
        if exc:
            logger.error("Job %s raised exception: %s", jid, exc)

    # Scan for new .job files
    try:
        job_files = sorted(spool.glob("*.job"))
    except OSError:
        return

    # Limit pending jobs to prevent DoS (max 100 in-flight)
    if len(pending_futures) >= 100:
        return

    for job_path in job_files:
        job_id = job_path.stem
        if job_id in locked_fds or job_id in pending_futures:
            continue

        # Skip symlinks — prevent injection via symlink to crafted file
        if job_path.is_symlink():
            logger.warning("Skipping symlink %s", job_path)
            continue

        # Skip oversized job files (DoS protection — 64KB max)
        try:
            if job_path.stat().st_size > 65536:
                logger.warning("Skipping oversized job file %s", job_path)
                job_path.unlink(missing_ok=True)
                continue
        except OSError:
            continue

        # Try to acquire lock
        try:
            fd = os.open(str(job_path), os.O_RDONLY)
        except OSError:
            continue

        if not _try_lock(fd):
            os.close(fd)
            continue

        # Read and validate job
        try:
            job = read_job(job_path)
        except Exception as exc:
            logger.error("Failed to read job file %s: %s", job_path, exc)
            # Quarantine: write error result and delete malformed file
            try:
                error_result = ResultFile(
                    id=job_id,
                    stderr=f"Malformed job file: {exc}",
                    exit_code=1,
                )
                write_result(error_result, spool)
            except Exception:
                pass
            job_path.unlink(missing_ok=True)
            _unlock(fd)
            os.close(fd)
            continue

        # Check if job has timed out before execution
        if time.time() - job.ts > job.timeout:
            logger.warning("Job %s expired before pickup (age=%.1fs)", job.id, time.time() - job.ts)
            timeout_result = ResultFile(
                id=job.id,
                stderr=f"Job expired before pickup (was pending for {time.time() - job.ts:.1f}s)",
                exit_code=124,
            )
            try:
                write_result(timeout_result, spool)
                job_path.unlink(missing_ok=True)
            except OSError as e:
                logger.error("Failed to write timeout result for %s: %s", job.id, e)
            _unlock(fd)
            os.close(fd)
            continue

        # Dispatch to worker pool
        locked_fds[job_id] = fd

        def _job_done(future: Future, jid: str = job_id, jp: Path = job_path, sp: Path = spool) -> None:
            try:
                result = future.result()
                write_result(result, sp)
                jp.unlink(missing_ok=True)
            except Exception as exc:
                logger.error("Error writing result for job %s: %s", jid, exc)

        future = pool.submit(execute_job, job, host_env, config.extra_blocked_executables)
        future.add_done_callback(_job_done)
        pending_futures[job_id] = future
