"""Resolve the host shell environment for command execution."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

PROTECTED_KEYS: frozenset[str] = frozenset({"HOME", "USER", "LOGNAME", "SHELL", "PATH"})

# Keys that can be used to inject code before/around command execution
DANGEROUS_ENV_KEYS: frozenset[str] = frozenset({
    # Shell hooks — sourced automatically by bash/zsh
    "BASH_ENV", "ENV", "CDPATH", "GLOBIGNORE", "BASH_XTRACEFD",
    "PROMPT_COMMAND", "PS0", "PS1", "PS4",
    # Dynamic linker — load arbitrary shared objects
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
    # Language startup hooks — execute code on interpreter start
    "PYTHONSTARTUP", "PYTHONPATH", "PYTHONHOME",
    "NODE_OPTIONS", "NODE_PATH",
    "PERL5OPT", "PERL5LIB", "PERLLIB",
    "RUBYOPT", "RUBYLIB",
    # IFS can split commands in unexpected ways
    "IFS",
})

# Prefixes that are dangerous (e.g. BASH_FUNC_xxx%%=...)
DANGEROUS_ENV_PREFIXES: tuple[str, ...] = ("BASH_FUNC_", "LD_", "DYLD_")

_cached_env: dict[str, str] | None = None


def resolve_host_env() -> dict[str, str]:
    """Resolve the host environment by sourcing login shell RC files.

    Result is cached for the process lifetime.
    """
    global _cached_env
    if _cached_env is not None:
        return _cached_env

    env = _source_login_shell()

    # macOS: also pick up /etc/paths and /etc/paths.d/*
    if platform.system() == "Darwin":
        env = _merge_macos_paths(env)

    _cached_env = env
    return _cached_env


def reset_cache() -> None:
    """Clear the cached env (for tests)."""
    global _cached_env
    _cached_env = None


def _source_login_shell() -> dict[str, str]:
    """Spawn a login shell and capture its resolved environment."""
    shell = os.environ.get("SHELL", "/bin/bash")
    shell_name = Path(shell).name

    if platform.system() == "Darwin" and shell_name == "zsh":
        shell_cmd = ["/bin/zsh", "--login", "-c", "env"]
    else:
        shell_cmd = ["/bin/bash", "--login", "-c", "env"]

    try:
        result = subprocess.run(
            shell_cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            env={"HOME": os.environ.get("HOME", ""), "TERM": "dumb"},
        )
        return _parse_env(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("Failed to source login shell environment: %s — using os.environ", exc)
        return dict(os.environ)


def _parse_env(raw: str) -> dict[str, str]:
    """Parse KEY=VALUE lines from `env` output.

    Handles multi-line values by only splitting on the first '='.
    Lines without '=' are appended to the previous value.
    """
    env: dict[str, str] = {}
    last_key: str | None = None
    for line in raw.splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            # Env var keys must be valid identifiers
            if key and (key[0].isalpha() or key[0] == "_") and all(c.isalnum() or c == "_" for c in key):
                env[key] = val
                last_key = key
            elif last_key:
                # Continuation of a multi-line value
                env[last_key] += "\n" + line
        elif last_key:
            env[last_key] += "\n" + line
    return env


def _merge_macos_paths(env: dict[str, str]) -> dict[str, str]:
    """Merge /etc/paths and /etc/paths.d/* into PATH."""
    extra_paths: list[str] = []

    etc_paths = Path("/etc/paths")
    if etc_paths.exists():
        for line in etc_paths.read_text().splitlines():
            stripped = line.strip()
            if stripped:
                extra_paths.append(stripped)

    paths_d = Path("/etc/paths.d")
    if paths_d.is_dir():
        for f in sorted(paths_d.iterdir()):
            if f.is_file():
                for line in f.read_text().splitlines():
                    stripped = line.strip()
                    if stripped:
                        extra_paths.append(stripped)

    if extra_paths:
        current = env.get("PATH", "").split(":")
        current_set = set(current)
        for p in extra_paths:
            if p not in current_set:
                current.append(p)
                current_set.add(p)
        env["PATH"] = ":".join(current)

    return env


def merge_job_env(host_env: dict[str, str], job_env: dict[str, str] | None) -> dict[str, str]:
    """Overlay job env on top of host env, dropping protected and dangerous keys."""
    merged = dict(host_env)
    if not job_env:
        return merged

    for key, val in job_env.items():
        if key in PROTECTED_KEYS:
            logger.warning("Dropping protected env key '%s' from job env", key)
            continue
        if key in DANGEROUS_ENV_KEYS or any(key.startswith(p) for p in DANGEROUS_ENV_PREFIXES):
            logger.warning("Dropping dangerous env key '%s' from job env", key)
            continue
        merged[key] = val

    return merged
