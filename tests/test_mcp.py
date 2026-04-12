"""Tests for hr.mcp_server — MCP tool integration tests."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from hr.spool import ResultFile, JobFile, write_result, write_job, spool_dir_path
from hr.mcp_server import HR_NOT_RUNNING, HR_TIMEOUT


# We test the MCP tools by calling their underlying logic directly,
# since the MCP transport layer (stdio) is tested by the mcp SDK.


class TestHostRunLogic:
    """Test host_run behavior via spool file manipulation."""

    def test_hr_not_running(self, tmp_path, monkeypatch):
        """When hr.pid doesn't exist, should return HR_NOT_RUNNING."""
        monkeypatch.setattr("hr.mcp_server.PID_FILE", tmp_path / "hr.pid")

        from hr.mcp_server import _hr_is_running
        assert _hr_is_running() is False

    def test_hr_running_check(self, tmp_path, monkeypatch):
        """When hr.pid has our own PID, should return True."""
        pid_file = tmp_path / "hr.pid"
        pid_file.write_text(str(os.getpid()))
        monkeypatch.setattr("hr.mcp_server.PID_FILE", pid_file)

        from hr.mcp_server import _hr_is_running
        assert _hr_is_running() is True

    def test_stale_pid(self, tmp_path, monkeypatch):
        """When hr.pid has a dead PID, should return False."""
        pid_file = tmp_path / "hr.pid"
        pid_file.write_text("99999999")
        monkeypatch.setattr("hr.mcp_server.PID_FILE", pid_file)

        from hr.mcp_server import _hr_is_running
        assert _hr_is_running() is False


class TestSpoolStatusLogic:
    def test_pending_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hr.mcp_server.SPOOL_DIR", tmp_path)
        monkeypatch.setattr("hr.mcp_server.PID_FILE", tmp_path / "hr.pid")
        monkeypatch.setattr("hr.mcp_server.spool_dir_path", lambda: tmp_path)

        (tmp_path / "A.job").write_text("{}")
        (tmp_path / "B.job").write_text("{}")

        import asyncio
        from hr.mcp_server import spool_status
        result = asyncio.run(spool_status())
        assert result["pending_jobs"] == 2
        assert result["hr_running"] is False

    def test_last_result_time(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hr.mcp_server.SPOOL_DIR", tmp_path)
        monkeypatch.setattr("hr.mcp_server.PID_FILE", tmp_path / "hr.pid")
        monkeypatch.setattr("hr.mcp_server.spool_dir_path", lambda: tmp_path)

        r = ResultFile(id="R1", stdout="ok")
        write_result(r, tmp_path)

        import asyncio
        from hr.mcp_server import spool_status
        result = asyncio.run(spool_status())
        assert result["last_result_time"] is not None


class TestAbortJobLogic:
    def test_abort_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hr.mcp_server.spool_dir_path", lambda: tmp_path)

        job = JobFile(id="ABORT1", cmd="echo x")
        write_job(job, tmp_path)
        assert (tmp_path / "ABORT1.job").exists()

        import asyncio
        from hr.mcp_server import abort_job
        result = asyncio.run(abort_job("ABORT1"))
        assert result["status"] == "aborted"
        assert not (tmp_path / "ABORT1.job").exists()

    def test_abort_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hr.mcp_server.spool_dir_path", lambda: tmp_path)

        import asyncio
        from hr.mcp_server import abort_job
        result = asyncio.run(abort_job("NONEXIST"))
        assert result["status"] == "not_found"
