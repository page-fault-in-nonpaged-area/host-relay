"""Tests for hr.worker — job execution."""

import os

import pytest

from hr.spool import JobFile, ResultFile
from hr.worker import execute_job


@pytest.fixture
def host_env():
    return dict(os.environ)


class TestExecuteJob:
    def test_echo(self, host_env):
        job = JobFile(id="W001", cmd="echo hello", timeout=10)
        result = execute_job(job, host_env)
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_policy_rejection(self, host_env):
        job = JobFile(id="W002", cmd="echo a; echo b", timeout=10)
        result = execute_job(job, host_env)
        assert result.exit_code == 126
        assert ";" in result.stderr or "not allowed" in result.stderr

    def test_nonzero_exit(self, host_env):
        job = JobFile(id="W003", cmd="false", timeout=10)
        result = execute_job(job, host_env)
        assert result.exit_code != 0

    def test_unknown_executable(self, host_env):
        job = JobFile(id="W004", cmd="nonexistent_binary_xyz_12345", timeout=10)
        result = execute_job(job, host_env)
        assert result.exit_code == 127 or result.exit_code != 0

    def test_env_overlay(self, host_env):
        job = JobFile(
            id="W005",
            cmd="printenv HR_TEST_VAR",
            env={"HR_TEST_VAR": "test_value_42"},
            timeout=10,
        )
        result = execute_job(job, host_env)
        assert result.exit_code == 0
        assert "test_value_42" in result.stdout

    def test_protected_key_drop(self, host_env):
        original_path = host_env.get("PATH", "/usr/bin")
        job = JobFile(
            id="W006",
            cmd="printenv PATH",
            env={"PATH": "/evil/path"},
            timeout=10,
        )
        result = execute_job(job, host_env)
        assert result.exit_code == 0
        assert "/evil/path" not in result.stdout

    def test_pipe(self, host_env):
        job = JobFile(id="W007", cmd="echo hello world | grep hello", timeout=10)
        result = execute_job(job, host_env)
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_multi_pipe(self, host_env):
        job = JobFile(id="W008", cmd="echo abc | cat | cat", timeout=10)
        result = execute_job(job, host_env)
        assert result.exit_code == 0
        assert "abc" in result.stdout

    def test_result_has_elapsed_ms(self, host_env):
        job = JobFile(id="W009", cmd="echo fast", timeout=10)
        result = execute_job(job, host_env)
        assert result.elapsed_ms > 0

    def test_stderr_captured(self, host_env):
        job = JobFile(id="W010", cmd="echo err >&2", timeout=10)
        result = execute_job(job, host_env)
        assert "err" in result.stderr

    def test_timeout(self, host_env):
        job = JobFile(id="W011", cmd="sleep 60", timeout=1)
        result = execute_job(job, host_env)
        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()

    def test_blocked_exec(self, host_env):
        job = JobFile(id="W012", cmd="bash -c 'echo hi'", timeout=10)
        result = execute_job(job, host_env)
        assert result.exit_code == 126

    def test_extra_blocked(self, host_env):
        job = JobFile(id="W013", cmd="custom_cmd arg", timeout=10)
        result = execute_job(job, host_env, extra_blocked=["custom_cmd"])
        assert result.exit_code == 126
