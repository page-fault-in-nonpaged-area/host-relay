"""Configuration for host-relay."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Use SNAP_REAL_HOME when running inside a snap — snap remaps $HOME to a
# versioned path (e.g. ~/snap/copilot-cli/17) while the host-side hr process
# uses the real home directory.  Both sides must agree on HR_DIR and SPOOL_DIR.
_real_home = Path(os.environ.get("SNAP_REAL_HOME") or Path.home())

HR_DIR = _real_home / ".host-relay"
LOGS_DIR = HR_DIR / "logs"
PID_FILE = HR_DIR / "hr.pid"
CONFIG_FILE = HR_DIR / "config.json"

# Spool lives in ~/host-relay — accessible by both the host process and any
# sandboxed agent (snap, container, etc.) that shares the user's home directory.
# $HOME is visible across snap confinement boundaries unlike /tmp, which snap
# mounts privately per-app.
# Override with HR_SPOOL_DIR if you need a non-standard location.
_spool_override = os.environ.get("HR_SPOOL_DIR")
SPOOL_DIR = Path(_spool_override) if _spool_override else _real_home / "host-relay"


@dataclass
class Config:
    worker_count: int = 4
    default_timeout: int = 30
    max_timeout: int = 120
    poll_interval_ms: int = 100
    result_poll_interval_ms: int = 50
    log_max_bytes: int = 10_485_760  # 10 MB
    log_backup_count: int = 3
    extra_blocked_executables: list[str] = field(default_factory=list)


def load_config() -> Config:
    """Load config from ~/.host-relay/config.json, falling back to defaults."""
    if not CONFIG_FILE.exists():
        return Config()

    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Invalid config at %s: %s — using defaults", CONFIG_FILE, exc)
        return Config()

    kwargs: dict = {}
    cfg = Config()
    for fld in cfg.__dataclass_fields__:
        if fld in raw:
            kwargs[fld] = raw[fld]

    try:
        return Config(**kwargs)
    except TypeError as exc:
        logger.error("Bad config values: %s — using defaults", exc)
        return Config()
