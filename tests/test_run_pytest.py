"""Regression test: a pytest run that ends in errors must never be recorded as a clean '0 passed, 0 failed'."""

from __future__ import annotations

import subprocess

from pipeline import run

COLLECTION_ERROR = """
==================================== ERRORS ====================================
___________________ ERROR collecting tests/test_match.py _______________________
ImportError while importing test module 'tests/test_match.py'.
E   ModuleNotFoundError: No module named 'duckdb'
=========================== short test summary info ============================
ERROR tests/test_match.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.41s
"""


def _fake_pytest(monkeypatch, stdout: str, returncode: int) -> None:
    monkeypatch.setattr(run.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout=stdout, stderr=""))


def test_collection_error_is_not_ok_and_exits_non_zero(monkeypatch, capsys):
    _fake_pytest(monkeypatch, COLLECTION_ERROR, returncode=2)
    t = run.run_pytest()
    assert t.get("ok") is False, f"a suite that never ran was recorded as ok: {t}"
    assert t["error"] == 1 and t["passed"] == 0
    assert run.check_tests(t) == 1
    assert "did not run cleanly" in capsys.readouterr().out


def test_bad_exit_code_without_error_count_is_not_ok(monkeypatch, capsys):
    _fake_pytest(monkeypatch, "no tests ran in 0.01s\n", returncode=5)
    t = run.run_pytest()
    assert t.get("ok") is False
    assert run.check_tests(t) == 1
    assert "exit code 5" in capsys.readouterr().out


def test_error_with_exit_code_1_is_still_not_ok(monkeypatch):
    _fake_pytest(monkeypatch, "..F.E\n1 failed, 3 passed, 1 error in 2.0s\n", returncode=1)
    t = run.run_pytest()
    assert t.get("ok") is False and (t["passed"], t["failed"], t["error"]) == (3, 1, 1)


def test_clean_run_is_ok(monkeypatch):
    _fake_pytest(monkeypatch, "....\n26 passed, 1 skipped in 30.1s\n", returncode=0)
    t = run.run_pytest()
    assert t["ok"] is True and (t["passed"], t["failed"], t["skipped"]) == (26, 0, 1)
    assert run.check_tests(t) == 0
