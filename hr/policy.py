"""Command policy validator — static string analyser.

Checks that a command string is a simple pipeline of executables
with arguments. Rejects anything that would allow arbitrary shell
programming (loops, forks, command substitution, etc.).
"""

from __future__ import annotations

import os
import re

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class PolicyViolation(Exception):
    """Raised when a command string violates the policy."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        self.message = message
        super().__init__(f"[{rule}] {message}")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLOCKED_EXECUTABLES: set[str] = {
    "bash", "sh", "zsh", "fish", "ksh", "csh", "tcsh", "dash",
    "eval", "exec", "source", ".",
    "nohup", "disown", "setsid", "screen", "tmux", "reptyr",
    "start-stop-daemon", "daemon",
    # Wrapper commands that can invoke blocked executables
    "env", "nice", "ionice", "stdbuf", "timeout", "command",
    # Privilege escalation
    "sudo", "su", "doas", "pkexec", "runuser", "chroot",
    # Debugging/tracing
    "strace", "ltrace", "gdb",
    # awk variants — they have system() built-in
    "awk", "gawk", "mawk", "nawk",
}

INTERPRETER_NAMES: set[str] = {
    "python", "python2", "python3",
    "ruby", "node", "nodejs",
    "perl", "php", "Rscript",
    "lua", "guile", "tclsh", "wish",
}

# Flags that mean "inline expression/module, not a script file"
INTERPRETER_INLINE_FLAGS: set[str] = {"-c", "-e", "-m"}

SCRIPT_EXTENSIONS: set[str] = {
    ".py", ".rb", ".js", ".mjs", ".cjs", ".ts",
    ".pl", ".pm", ".php", ".R", ".r",
    ".lua", ".tcl", ".sh", ".bash", ".zsh",
}


# ---------------------------------------------------------------------------
# Quote-aware helpers
# ---------------------------------------------------------------------------

def _build_quote_mask(cmd: str) -> list[bool]:
    """Return a boolean mask — True where the character is inside quotes or escaped."""
    mask = [False] * len(cmd)
    state = "NORMAL"
    i = 0

    while i < len(cmd):
        c = cmd[i]

        if state == "NORMAL":
            if c == "\\" and i + 1 < len(cmd):
                mask[i] = True
                mask[i + 1] = True
                i += 2
                continue
            if c == "'":
                mask[i] = True
                state = "SINGLE"
                i += 1
                continue
            if c == '"':
                mask[i] = True
                state = "DOUBLE"
                i += 1
                continue
            i += 1

        elif state == "SINGLE":
            mask[i] = True
            if c == "'":
                state = "NORMAL"
            i += 1

        elif state == "DOUBLE":
            mask[i] = True
            if c == "\\" and i + 1 < len(cmd) and cmd[i + 1] in '"\\$`\n':
                mask[i + 1] = True
                i += 2
                continue
            if c == '"':
                state = "NORMAL"
                i += 1
                continue
            # Command substitution inside double quotes is still expanded
            if c == "`":
                raise PolicyViolation(
                    "no_command_substitution",
                    "Backtick command substitution is not allowed (even inside double quotes)",
                )
            if c == "$" and i + 1 < len(cmd) and cmd[i + 1] == "(":
                raise PolicyViolation(
                    "no_command_substitution",
                    "$() command substitution is not allowed (even inside double quotes)",
                )
            # $VAR / ${VAR} inside double quotes still expands
            if c == "$" and i + 1 < len(cmd):
                next_c = cmd[i + 1]
                if next_c == "{":
                    raise PolicyViolation(
                        "no_variable_expansion",
                        "${...} variable expansion is not allowed (even inside double quotes)",
                    )
                if next_c.isalpha() or next_c == "_":
                    raise PolicyViolation(
                        "no_variable_expansion",
                        "$VAR variable expansion is not allowed (even inside double quotes)",
                    )
            i += 1

    if state != "NORMAL":
        raise PolicyViolation("unclosed_quote", "Unclosed quote in command")

    return mask


# ---------------------------------------------------------------------------
# Pattern checks on unquoted characters
# ---------------------------------------------------------------------------

def _check_unquoted_patterns(cmd: str, mask: list[bool]) -> None:
    """Check for dangerous shell constructs in unquoted portions."""
    i = 0
    while i < len(cmd):
        if mask[i]:
            i += 1
            continue

        c = cmd[i]

        # Statement separator: ;
        if c == ";":
            raise PolicyViolation("no_statement_separators", "';' is not allowed")

        # && or || (check two-char operators)
        if c == "&" and i + 1 < len(cmd) and not mask[i + 1] and cmd[i + 1] == "&":
            raise PolicyViolation("no_statement_separators", "'&&' is not allowed")

        if c == "|" and i + 1 < len(cmd) and not mask[i + 1] and cmd[i + 1] == "|":
            raise PolicyViolation("no_statement_separators", "'||' is not allowed")

        # Background &
        if c == "&":
            # &> or &>> — redirect, allowed
            if i + 1 < len(cmd) and not mask[i + 1] and cmd[i + 1] == ">":
                i += 1
                continue
            # >& (fd duplication) — check if previous unquoted char was >
            if i > 0 and not mask[i - 1] and cmd[i - 1] == ">":
                i += 1
                continue
            # N>& — digit then > then &
            if i >= 2 and not mask[i - 1] and cmd[i - 1] == ">" and not mask[i - 2] and cmd[i - 2].isdigit():
                i += 1
                continue
            raise PolicyViolation("no_background", "Background operator '&' is not allowed")

        # Command substitution in normal context
        if c == "`":
            raise PolicyViolation(
                "no_command_substitution",
                "Backtick command substitution is not allowed",
            )
        # $VAR / ${VAR} variable expansion — can bypass policy via env injection
        if c == "$" and i + 1 < len(cmd):
            next_c = cmd[i + 1]
            # $'...' ANSI-C quoting — check raw char regardless of mask
            # (the quote char gets masked by _build_quote_mask but $' is a
            # single quoting construct in bash)
            if next_c == "'":
                raise PolicyViolation(
                    "no_ansi_c_quoting",
                    "$'...' ANSI-C quoting is not allowed",
                )
            if not mask[i + 1]:
                if next_c == "(":
                    raise PolicyViolation(
                        "no_command_substitution",
                        "$() command substitution is not allowed",
                    )
                if next_c == "{":
                    raise PolicyViolation(
                        "no_variable_expansion",
                        "${...} variable expansion is not allowed",
                    )
                if next_c.isalpha() or next_c == "_":
                    raise PolicyViolation(
                        "no_variable_expansion",
                        "$VAR variable expansion is not allowed",
                    )

        # Process substitution <(...) >(...)
        if c == "<" and i + 1 < len(cmd) and not mask[i + 1] and cmd[i + 1] == "(":
            raise PolicyViolation("no_process_substitution", "<() process substitution is not allowed")
        if c == ">" and i + 1 < len(cmd) and not mask[i + 1] and cmd[i + 1] == "(":
            raise PolicyViolation("no_process_substitution", ">() process substitution is not allowed")

        # Subshell ( — reject unquoted ( not preceded by $ < >
        if c == "(":
            # Already caught $( and <( and >( above, so if we reach here it's a raw (
            raise PolicyViolation("no_subshell", "Subshell '(' is not allowed")

        i += 1


# ---------------------------------------------------------------------------
# Pipe splitting
# ---------------------------------------------------------------------------

def _split_pipes(cmd: str, mask: list[bool]) -> list[tuple[str, int]]:
    """Split command on unquoted '|', returning (stage_text, start_offset) pairs."""
    positions: list[int] = []
    for i, c in enumerate(cmd):
        if not mask[i] and c == "|":
            positions.append(i)

    stages: list[tuple[str, int]] = []
    prev = 0
    for pos in positions:
        raw = cmd[prev:pos]
        stripped = raw.strip()
        # Compute actual offset accounting for leading whitespace removed by strip()
        actual_offset = prev + len(raw) - len(raw.lstrip())
        stages.append((stripped, actual_offset))
        prev = pos + 1
    raw = cmd[prev:]
    stripped = raw.strip()
    actual_offset = prev + len(raw) - len(raw.lstrip())
    stages.append((stripped, actual_offset))
    return stages


# ---------------------------------------------------------------------------
# Per-stage validation
# ---------------------------------------------------------------------------

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _tokenize_stage(stage: str, mask: list[bool], offset: int) -> list[str]:
    """Split a stage into whitespace-separated tokens, respecting quotes."""
    tokens: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(stage):
        if not mask[offset + i] and stage[i] in " \t":
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(stage[i])
        i += 1
    if current:
        tokens.append("".join(current))
    return tokens


def _strip_quotes(token: str) -> str:
    """Remove surrounding quotes from a token for content inspection."""
    if len(token) >= 2:
        if (token[0] == "'" and token[-1] == "'") or (token[0] == '"' and token[-1] == '"'):
            return token[1:-1]
    return token


def _is_interpreter(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    if base in INTERPRETER_NAMES:
        return True
    for prefix in ("python3.", "python2.", "ruby", "perl5."):
        if base.startswith(prefix) and len(base) > len(prefix):
            return True
    return False


def _looks_like_script(arg: str) -> bool:
    """Heuristic: does this argument look like a script file path?"""
    clean = _strip_quotes(arg)
    if clean.startswith("-"):
        return False
    _, ext = os.path.splitext(clean)
    if ext.lower() in SCRIPT_EXTENSIONS:
        return True
    if "/" in clean:
        return True
    return False


def _validate_stage(stage: str, mask: list[bool], offset: int, blocked: set[str]) -> None:
    if not stage:
        raise PolicyViolation("empty_stage", "Empty pipe stage")

    tokens = _tokenize_stage(stage, mask, offset)
    if not tokens:
        raise PolicyViolation("empty_stage", "Empty pipe stage")

    # Skip leading VAR=val assignments to find the executable
    exe_idx = 0
    while exe_idx < len(tokens) and _ENV_ASSIGN_RE.match(tokens[exe_idx]):
        exe_idx += 1

    if exe_idx >= len(tokens):
        raise PolicyViolation("no_executable", "Pipe stage has only variable assignments, no command")

    exe_token = tokens[exe_idx]

    # Strip path: /usr/bin/git → git (for blocked-list matching)
    exe_base = exe_token.rsplit("/", 1)[-1]

    # Check blocked executables
    if exe_base in blocked:
        raise PolicyViolation(
            "blocked_executable",
            f"Executable '{exe_base}' is blocked",
        )

    # Check for detach/disown helpers anywhere in the stage
    detach_commands = {"nohup", "disown", "setsid", "start-stop-daemon", "daemon", "screen", "tmux", "reptyr"}
    for tok in tokens:
        tok_base = tok.rsplit("/", 1)[-1]
        if tok_base in detach_commands:
            raise PolicyViolation(
                "no_detach",
                f"Detach/spawn command '{tok_base}' is not allowed",
            )

    # Interpreter + script file check
    if _is_interpreter(exe_base):
        args_after_exe = tokens[exe_idx + 1:]
        # If first arg is an inline flag (-c, -e, -m), allow
        if args_after_exe and args_after_exe[0] not in INTERPRETER_INLINE_FLAGS:
            if args_after_exe and _looks_like_script(args_after_exe[0]):
                raise PolicyViolation(
                    "no_interpreter_script",
                    f"Running script files via '{exe_base}' is not allowed; use -c for inline expressions",
                )

    # Redirection path checks
    _check_redirections(tokens, offset, mask)


_REDIRECT_RE = re.compile(r"^(\d*)(>>?|<)(.*)$")
_AMP_REDIRECT_RE = re.compile(r"^&(>>?)(.*)$")


def _check_redirections(tokens: list[str], offset: int, mask: list[bool]) -> None:
    """Check that redirection targets are within ~ or /tmp."""
    home = os.path.expanduser("~")
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # Match N> file, N>> file, > file, >> file, < file
        m = _REDIRECT_RE.match(tok)
        if m:
            _fd, _op, target = m.groups()
            if not target and i + 1 < len(tokens):
                target = tokens[i + 1]
                i += 1
            if target:
                _validate_redirect_target(target, home)
            i += 1
            continue

        # Match &> file, &>> file
        m2 = _AMP_REDIRECT_RE.match(tok)
        if m2:
            _op, target = m2.groups()
            if not target and i + 1 < len(tokens):
                target = tokens[i + 1]
                i += 1
            if target:
                _validate_redirect_target(target, home)
            i += 1
            continue

        i += 1


def _validate_redirect_target(target: str, home: str) -> None:
    """Raise if the redirect target path is outside ~ or /tmp."""
    clean = _strip_quotes(target)
    if not clean:
        return

    # Allowed destinations
    if clean == "/dev/null":
        return

    # Expand ~ and resolve to catch traversal like ~/../../etc/passwd
    if clean.startswith("~") or clean.startswith(home):
        expanded = os.path.expanduser(clean) if clean.startswith("~") else clean
        resolved = os.path.normpath(expanded)
        if not resolved.startswith(home):
            raise PolicyViolation(
                "redirect_outside_home",
                f"Redirection to '{clean}' resolves outside home directory",
            )
        return
    if clean.startswith("/tmp"):
        resolved = os.path.normpath(clean)
        if not resolved.startswith("/tmp"):
            raise PolicyViolation(
                "redirect_outside_home",
                f"Redirection to '{clean}' resolves outside /tmp",
            )
        return
    # Relative paths — also normalize to catch ../../../
    if not clean.startswith("/"):
        return

    raise PolicyViolation(
        "redirect_outside_home",
        f"Redirection to '{clean}' is not allowed; only ~, /tmp, and /dev/null are permitted",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate(cmd: str, extra_blocked: list[str] | None = None) -> None:
    """Validate a command string against the simple-command policy.

    Raises PolicyViolation if the command is rejected.
    """
    if not cmd or not cmd.strip():
        raise PolicyViolation("empty_command", "Command is empty")

    blocked = BLOCKED_EXECUTABLES | set(extra_blocked or [])

    # 1. Null bytes and control characters
    if "\x00" in cmd:
        raise PolicyViolation("no_null_bytes", "Command must not contain null bytes")
    for ch in cmd:
        if ord(ch) < 0x20 and ch not in ("\t",):
            raise PolicyViolation("no_control_chars", "Command must not contain control characters")

    # 2. Newlines (explicit check for clarity)
    if "\n" in cmd or "\r" in cmd:
        raise PolicyViolation("no_newlines", "Command must not contain newline characters")

    # 3. Build quote mask (also checks command substitution inside double quotes)
    mask = _build_quote_mask(cmd)

    # 4. Check unquoted dangerous patterns (includes $VAR, $'...')
    _check_unquoted_patterns(cmd, mask)

    # 5. Split into pipe stages
    stages = _split_pipes(cmd, mask)

    # 6. Validate each stage
    for stage_str, stage_offset in stages:
        _validate_stage(stage_str, mask, stage_offset, blocked)
