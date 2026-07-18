"""End-to-end demo test against a real Docker sandbox.

Skipped automatically when no Docker daemon is reachable, so the default test
run stays hermetic.  When Docker is present this exercises the real install
phase, double execution, adjudication and receipt verification against the
bundled toy repository, with a scripted LLM (no live API credential needed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from liedetector.cli import run_pipeline
from liedetector.executor import DockerExecutor, docker_available
from liedetector.receipt import verify_receipt

from .conftest import FakeLLM, extraction_response, harness_response

pytestmark = pytest.mark.docker

ADD_HARNESS = '''"""Verifies: toylib.add(2, 3) == 5."""


def test_control():
    import toylib  # control assertion
    assert toylib is not None


def test_claim():
    import toylib
    assert toylib.add(2, 3) == 5
'''

COUNT_HARNESS = '''"""Verifies: toylib.count_words("a  b") == 2."""


def test_control():
    import toylib  # control assertion
    assert toylib is not None


def test_claim():
    import toylib
    assert toylib.count_words("a  b") == 2
'''


@pytest.fixture(autouse=True)
def _require_docker() -> None:
    ok, detail = docker_available()
    if not ok:
        pytest.skip(f"docker unavailable: {detail}")


def test_e2e_demo_real_sandbox(tmp_path: Path, toy_repo_dir: Path) -> None:
    llm = FakeLLM(
        [
            extraction_response(
                [
                    {"quote": "`toylib.add(2, 3)` returns `5`.",
                     "claim_type": "deterministic", "hypothesis": "add(2,3)==5"},
                    {"quote": '`toylib.count_words("a  b")` returns `2`.',
                     "claim_type": "deterministic", "hypothesis": "count_words('a  b')==2"},
                ]
            ),
            harness_response(ADD_HARNESS),
            harness_response(COUNT_HARNESS),
        ]
    )
    result = run_pipeline(
        str(toy_repo_dir),
        llm=llm,
        executor=DockerExecutor(),
        receipts_dir=tmp_path / "receipts",
        reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses",
        allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    # add(2,3)==5 is genuinely true; count_words("a  b")==2 is genuinely false.
    assert result.verdict_tally["PROVEN"] == 1
    assert result.verdict_tally["FALSE"] == 1
    checks = verify_receipt(result.receipt_path)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
