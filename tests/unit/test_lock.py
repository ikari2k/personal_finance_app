"""Tests for the PID-based file lock in app.storage.lock."""

import os
import subprocess
import sys

import pytest

from app.storage.lock import LockError, file_lock


def test_file_lock_creates_and_removes_lock_file(tmp_path):
    target = tmp_path / "ledger.csv"
    lock_path = tmp_path / "ledger.csv.lock"

    with file_lock(target):
        assert lock_path.exists()
        assert lock_path.read_text().strip() == str(os.getpid())

    assert not lock_path.exists()


def test_file_lock_raises_when_held_by_a_live_process(tmp_path):
    target = tmp_path / "ledger.csv"
    lock_path = tmp_path / "ledger.csv.lock"
    lock_path.write_text(str(os.getpid()))

    with pytest.raises(LockError):
        with file_lock(target, timeout=0.2, poll_interval=0.05):
            pass

    lock_path.unlink()


def test_file_lock_clears_a_stale_lock_from_a_dead_pid(tmp_path):
    target = tmp_path / "ledger.csv"
    lock_path = tmp_path / "ledger.csv.lock"

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    lock_path.write_text(str(proc.pid))

    with file_lock(target, timeout=1.0):
        assert lock_path.read_text().strip() == str(os.getpid())

    assert not lock_path.exists()
