"""Unit tests for pytest output parsing and executor argument construction."""

from __future__ import annotations

from liedetector.executor import DockerExecutor, parse_pytest_results


def test_parse_both_passed() -> None:
    out = "h.py::test_control PASSED\nh.py::test_claim PASSED\n"
    assert parse_pytest_results(out) == (True, True)


def test_parse_claim_failed() -> None:
    out = "h.py::test_control PASSED\nh.py::test_claim FAILED\n"
    assert parse_pytest_results(out) == (True, False)


def test_parse_control_error() -> None:
    out = "h.py::test_control ERROR\n"
    assert parse_pytest_results(out) == (False, None)


def test_parse_missing_results() -> None:
    assert parse_pytest_results("collected 0 items") == (None, None)


def test_image_digest_extracted() -> None:
    ex = DockerExecutor("python:3.12-slim@sha256:" + "a" * 64)
    assert ex.image_digest == "sha256:" + "a" * 64
