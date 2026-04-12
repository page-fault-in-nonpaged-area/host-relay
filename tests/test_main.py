"""Tests for hr.main — CLI entrypoint."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from hr.main import check_pid, cmd_status, _pid_is_alive


@pytest.fixture
def hr_dir(tmp_path, monkeypatch):
    """Set up a temporary .host-relay directory."""
    monkeypatch.setattr("hr.main.PID_FILE", tmp_path / "hr.pid")
    monkeypatch.setattr("hr.main.SPOOL_DIR", tmp_path / "spool")
    monkeypatch.setattr("hr.main.HR_DIR", tmp_path)
    (tmp_path / "spool").mkdir()
    return tmp_path


class TestCheckPid:
    def test_no_pid_file(self, hr_dir):
        # Should return without error
        check_pid()

    def test_stale_pid(self, hr_dir):
        pid_file = hr_dir / "hr.pid"
        pid_file.write_text("99999999")  # Very unlikely to be a real PID
        check_pid()
        assert not pid_file.exists(), "Stale PID file should be removed"

    def test_invalid_pid_file(self, hr_dir):
        pid_file = hr_dir / "hr.pid"
        pid_file.write_text("not_a_number")
        check_pid()
        assert not pid_file.exists()

    def test_running_pid_exits(self, hr_dir):
        pid_file = hr_dir / "hr.pid"
        pid_file.write_text(str(os.getpid()))  # Current process is alive
        with pytest.raises(SystemExit):
            check_pid()


class TestPidIsAlive:
    def test_current_process(self):
        assert _pid_is_alive(os.getpid()) is True

    def test_dead_process(self):
        assert _pid_is_alive(99999999) is False


class TestCmdStatus:
    def test_not_running(self, hr_dir, capsys):
        cmd_status()
        out = capsys.readouterr().out
        assert "not running" in out

    def test_stale_pid(self, hr_dir, capsys):
        (hr_dir / "hr.pid").write_text("99999999")
        cmd_status()
        out = capsys.readouterr().out
        assert "not running" in out

    def test_shows_pending_count(self, hr_dir, capsys):
        spool = hr_dir / "spool"
        (spool / "TEST.job").write_text("{}")
        cmd_status()
        out = capsys.readouterr().out
        assert "Pending jobs: 1" in out
