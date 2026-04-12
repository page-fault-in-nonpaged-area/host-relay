"""Tests for hr.spool — spool file I/O and cleanup."""

import json
import os
import time
from pathlib import Path

import pytest

from hr.spool import (
    JobFile,
    ResultFile,
    cleanup_orphans,
    generate_ulid,
    read_job,
    read_result,
    spool_dir_path,
    write_job,
    write_result,
)


def test_generate_ulid_unique():
    ids = {generate_ulid() for _ in range(100)}
    assert len(ids) == 100


def test_generate_ulid_length():
    ulid = generate_ulid()
    assert len(ulid) == 26


def test_generate_ulid_sortable():
    a = generate_ulid()
    time.sleep(0.002)
    b = generate_ulid()
    assert a < b


def test_job_roundtrip(tmp_path):
    job = JobFile(id="TEST1230000000000000000000", cmd="echo hello", env={"FOO": "bar"}, timeout=10)
    path = write_job(job, tmp_path)
    assert path.exists()
    assert path.suffix == ".job"

    loaded = read_job(path)
    assert loaded.id == job.id
    assert loaded.cmd == job.cmd
    assert loaded.env == job.env
    assert loaded.timeout == job.timeout


def test_result_roundtrip(tmp_path):
    result = ResultFile(id="TEST4560000000000000000000", stdout="out", stderr="err", exit_code=0, elapsed_ms=12.5)
    path = write_result(result, tmp_path)
    assert path.exists()
    assert path.suffix == ".result"

    loaded = read_result(path)
    assert loaded.id == result.id
    assert loaded.stdout == result.stdout
    assert loaded.stderr == result.stderr
    assert loaded.exit_code == result.exit_code
    assert loaded.elapsed_ms == result.elapsed_ms


def test_atomic_write_result(tmp_path):
    """Result file should not appear as a .result until rename is done."""
    result = ResultFile(id="ATOM1000000000000000000000", stdout="data")
    expected = tmp_path / "ATOM1000000000000000000000.result"

    # Before write, no .result exists
    assert not expected.exists()
    write_result(result, tmp_path)
    # After write, .result exists and .tmp does not
    assert expected.exists()
    assert not expected.with_suffix(".result.tmp").exists()


def test_file_permissions(tmp_path):
    job = JobFile(id="PERM1000000000000000000000", cmd="ls")
    path = write_job(job, tmp_path)
    stat = os.stat(path)
    assert stat.st_mode & 0o777 == 0o600


def test_cleanup_orphans_removes_old(tmp_path):
    old_job = tmp_path / "OLD1.job"
    old_job.write_text('{"id":"OLD1","cmd":"old"}')
    # Backdate the file
    old_time = time.time() - 600
    os.utime(old_job, (old_time, old_time))

    new_job = tmp_path / "NEW1.job"
    new_job.write_text('{"id":"NEW1","cmd":"new"}')

    removed = cleanup_orphans(tmp_path, max_age_seconds=300)
    assert removed == 1
    assert not old_job.exists()
    assert new_job.exists()


def test_cleanup_orphans_keeps_young(tmp_path):
    young = tmp_path / "YOUNG1.job"
    young.write_text('{"id":"YOUNG1","cmd":"young"}')
    removed = cleanup_orphans(tmp_path, max_age_seconds=300)
    assert removed == 0
    assert young.exists()


def test_spool_dir_path_creates(tmp_path, monkeypatch):
    target = tmp_path / "custom_spool"
    d = spool_dir_path(target)
    assert d.exists()
    assert d == target


def test_job_json_schema(tmp_path):
    """Verify the JSON structure matches design.md schema."""
    job = JobFile(id="SCHM1000000000000000000000", cmd="gh repo list", env={"GH_TOKEN": "abc"}, timeout=30)
    path = write_job(job, tmp_path)
    raw = json.loads(path.read_text())
    assert set(raw.keys()) == {"id", "cmd", "env", "timeout", "ts"}
    assert isinstance(raw["ts"], float)


def test_result_json_schema(tmp_path):
    result = ResultFile(id="SCHM2000000000000000000000", stdout="ok", stderr="", exit_code=0, elapsed_ms=5.0)
    path = write_result(result, tmp_path)
    raw = json.loads(path.read_text())
    assert set(raw.keys()) == {"id", "stdout", "stderr", "exit_code", "elapsed_ms", "ts"}
