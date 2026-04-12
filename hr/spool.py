"""Spool file I/O — job and result files in ~/.host-relay/spool/."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from hr.config import SPOOL_DIR

# ---------------------------------------------------------------------------
# ULID generation (avoids external dependency)
# ---------------------------------------------------------------------------
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_VALID_ID_RE = re.compile(r"^[A-Z0-9]{26}$")


def generate_ulid() -> str:
    ts = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    t_chars: list[str] = []
    for _ in range(10):
        t_chars.append(_CROCKFORD[ts & 0x1F])
        ts >>= 5
    t_chars.reverse()
    r_chars: list[str] = []
    for _ in range(16):
        r_chars.append(_CROCKFORD[rand & 0x1F])
        rand >>= 5
    r_chars.reverse()
    return "".join(t_chars) + "".join(r_chars)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class JobFile:
    id: str
    cmd: str
    env: dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    ts: float = field(default_factory=time.time)


@dataclass
class ResultFile:
    id: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    elapsed_ms: float = 0.0
    ts: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Spool directory
# ---------------------------------------------------------------------------
def spool_dir_path(spool_dir: Path | None = None) -> Path:
    """Return (and lazily create) the spool directory."""
    d = spool_dir or SPOOL_DIR
    d.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Verify permissions — refuse group/world accessible spool
    mode = d.stat().st_mode & 0o077
    if mode != 0:
        try:
            d.chmod(0o700)
        except OSError:
            pass
    return d


# ---------------------------------------------------------------------------
# Write / read helpers
# ---------------------------------------------------------------------------
def _write_atomic(path: Path, data: str) -> None:
    """Write *data* to *path* atomically via temp + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(str(tmp), str(path))


def write_job(job: JobFile, spool_dir: Path | None = None) -> Path:
    d = spool_dir_path(spool_dir)
    path = d / f"{job.id}.job"
    _write_atomic(path, json.dumps(asdict(job), ensure_ascii=False))
    return path


def read_job(path: Path) -> JobFile:
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Filter to known fields so extra keys don't cause TypeError
    known = {f.name for f in fields(JobFile)}
    filtered = {k: v for k, v in raw.items() if k in known}
    job = JobFile(**filtered)
    if not _VALID_ID_RE.match(job.id):
        raise ValueError(f"Invalid job ID: {job.id!r}")
    return job


def write_result(result: ResultFile, spool_dir: Path | None = None) -> Path:
    d = spool_dir_path(spool_dir)
    path = d / f"{result.id}.result"
    _write_atomic(path, json.dumps(asdict(result), ensure_ascii=False))
    return path


def read_result(path: Path) -> ResultFile:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ResultFile(**raw)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def cleanup_orphans(spool_dir: Path | None = None, max_age_seconds: int = 300) -> int:
    """Delete .job and .result files older than *max_age_seconds*. Returns count deleted."""
    d = spool_dir_path(spool_dir)
    now = time.time()
    removed = 0
    for child in d.iterdir():
        if child.suffix in (".job", ".result") or child.name.endswith(".tmp"):
            try:
                if now - child.stat().st_mtime > max_age_seconds:
                    child.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass
    return removed
