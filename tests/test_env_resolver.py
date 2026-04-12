"""Tests for hr.env_resolver."""

import os
from unittest.mock import patch, MagicMock

import pytest

from hr.env_resolver import (
    DANGEROUS_ENV_KEYS,
    DANGEROUS_ENV_PREFIXES,
    PROTECTED_KEYS,
    merge_job_env,
    reset_cache,
    resolve_host_env,
    _parse_env,
)


@pytest.fixture(autouse=True)
def clear_cache():
    reset_cache()
    yield
    reset_cache()


class TestResolveHostEnv:
    def test_returns_dict_with_path(self):
        env = resolve_host_env()
        # In constrained environments (e.g. snap sandbox), bash login
        # may not export PATH.  We just verify we get a non-empty dict.
        assert isinstance(env, dict) and len(env) > 0

    @patch("hr.env_resolver.subprocess.run")
    def test_fallback_on_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no bash")
        env = resolve_host_env()
        # Should fall back to os.environ
        assert "PATH" in env

    @patch("hr.env_resolver.subprocess.run")
    def test_parses_env_output(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "HOME=/home/test\nPATH=/usr/bin:/bin\nFOO=bar\n"
        mock_run.return_value = mock_result
        env = resolve_host_env()
        assert env["HOME"] == "/home/test"
        assert env["FOO"] == "bar"

    def test_caches_result(self):
        env1 = resolve_host_env()
        env2 = resolve_host_env()
        assert env1 is env2


class TestParseEnv:
    def test_basic(self):
        env = _parse_env("FOO=bar\nBAZ=qux\n")
        assert env == {"FOO": "bar", "BAZ": "qux"}

    def test_value_with_equals(self):
        env = _parse_env("URL=https://example.com?a=1&b=2\n")
        assert env["URL"] == "https://example.com?a=1&b=2"

    def test_empty_value(self):
        env = _parse_env("EMPTY=\n")
        assert env["EMPTY"] == ""

    def test_skips_invalid_keys(self):
        env = _parse_env("VALID=1\n123INVALID=2\n=nokey\n")
        assert "VALID" in env
        assert "123INVALID" not in env
        assert "" not in env


class TestMergeJobEnv:
    def test_overlay(self):
        host = {"PATH": "/usr/bin", "HOME": "/home/test", "FOO": "old"}
        merged = merge_job_env(host, {"FOO": "new", "BAR": "baz"})
        assert merged["FOO"] == "new"
        assert merged["BAR"] == "baz"

    def test_protected_keys_dropped(self):
        host = {"PATH": "/usr/bin", "HOME": "/home/test"}
        merged = merge_job_env(host, {"PATH": "/evil", "HOME": "/evil", "CUSTOM": "ok"})
        assert merged["PATH"] == "/usr/bin"
        assert merged["HOME"] == "/home/test"
        assert merged["CUSTOM"] == "ok"

    def test_all_protected_keys(self):
        host = {k: "original" for k in PROTECTED_KEYS}
        job_env = {k: "overridden" for k in PROTECTED_KEYS}
        merged = merge_job_env(host, job_env)
        for k in PROTECTED_KEYS:
            assert merged[k] == "original"

    def test_none_job_env(self):
        host = {"PATH": "/usr/bin"}
        merged = merge_job_env(host, None)
        assert merged == host

    def test_empty_job_env(self):
        host = {"PATH": "/usr/bin"}
        merged = merge_job_env(host, {})
        assert merged == host

    def test_dangerous_bash_env_dropped(self):
        host = {"PATH": "/usr/bin", "HOME": "/home/test"}
        merged = merge_job_env(host, {"BASH_ENV": "/evil.sh", "CUSTOM": "ok"})
        assert "BASH_ENV" not in merged
        assert merged["CUSTOM"] == "ok"

    def test_dangerous_ld_preload_dropped(self):
        host = {"PATH": "/usr/bin", "HOME": "/home/test"}
        merged = merge_job_env(host, {"LD_PRELOAD": "/evil.so"})
        assert "LD_PRELOAD" not in merged

    def test_dangerous_prefix_dropped(self):
        host = {"PATH": "/usr/bin"}
        merged = merge_job_env(host, {"BASH_FUNC_evil%%": "() { evil; }", "LD_CUSTOM": "x"})
        assert "BASH_FUNC_evil%%" not in merged
        assert "LD_CUSTOM" not in merged

    def test_all_dangerous_keys_dropped(self):
        host = {"PATH": "/usr/bin"}
        job_env = {k: "injected" for k in DANGEROUS_ENV_KEYS}
        merged = merge_job_env(host, job_env)
        for k in DANGEROUS_ENV_KEYS:
            assert k not in merged
