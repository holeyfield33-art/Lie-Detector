"""Shared fixtures: fake LLM client, fake executor, toy repository builders."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from liedetector.models import ExecutionRun, InstallResult

TOY_REPO = Path(__file__).resolve().parent.parent / "demo" / "toy_repo"

PASSING_HARNESS = '''"""Verifies: {hypothesis}"""


def test_control():
    import {package}  # control assertion
    assert {package} is not None


def test_claim():
    assert True  # EXPECT_PASS
'''

FAILING_HARNESS = '''"""Verifies: {hypothesis}"""


def test_control():
    import {package}  # control assertion
    assert {package} is not None


def test_claim():
    assert False  # EXPECT_FAIL
'''

# A harness that violates the static safety scan (forbidden ``socket`` import).
UNSAFE_HARNESS = (
    "import socket\n\n"
    "def test_control():\n"
    "    import {package}\n"
    "    assert {package}\n\n"
    "def test_claim():\n"
    "    assert True\n"
)


class FakeLLM:
    """Deterministic scripted LLM client; returns queued responses in order."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        return self.responses.pop(0)


def make_run(
    run_index: int = 1,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    control_passed: bool | None = True,
    claim_passed: bool | None = True,
) -> ExecutionRun:
    return ExecutionRun(
        run_index=run_index,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        control_passed=control_passed,
        claim_passed=claim_passed,
    )


class FakeExecutor:
    """Sandbox stand-in: harnesses marked EXPECT_PASS pass, EXPECT_FAIL fail."""

    def __init__(self, install_ok: bool = True) -> None:
        self.install_ok = install_ok
        self.installed_from: Path | None = None
        self.cleaned_up = False

    @property
    def image_digest(self) -> str:
        return "sha256:" + "f" * 64

    def install(self, repo_path: Path) -> InstallResult:
        self.installed_from = repo_path
        log = "# install exit_code=0 timed_out=False\nok" if self.install_ok else "boom"
        return InstallResult(
            ok=self.install_ok, exit_code=0 if self.install_ok else 1, log=log
        )

    def run_harness(self, harness_path: Path, run_index: int) -> ExecutionRun:
        code = harness_path.read_text(encoding="utf-8")
        passes = "EXPECT_PASS" in code
        stdout = (
            f"{harness_path.name}::test_control PASSED\n"
            f"{harness_path.name}::test_claim {'PASSED' if passes else 'FAILED'}\n"
        )
        return make_run(
            run_index=run_index,
            exit_code=0 if passes else 1,
            stdout=stdout,
            control_passed=True,
            claim_passed=passes,
        )

    def cleanup(self) -> None:
        self.cleaned_up = True


def extraction_response(claims: list[dict[str, Any]]) -> str:
    """Build a schema-valid extraction JSON response."""
    defaults = {
        "interpretation_notes": "literal reading",
        "confidence": "high",
        "suggested_strategy": "",
    }
    return json.dumps({"claims": [{**defaults, **claim} for claim in claims]})


def harness_response(code: str) -> str:
    return json.dumps({"harness_code": code})


def git_init_repo(path: Path) -> str:
    """Turn a directory into a git repo with one deterministic commit."""
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        "HOME": str(path),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    for cmd in (
        ["git", "init", "--quiet", "--initial-branch=main"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=path, env=env, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, env=env, capture_output=True, text=True
    ).stdout.strip()
    return sha


@pytest.fixture()
def toy_repo_dir(tmp_path: Path) -> Path:
    """A git-initialised copy of the bundled toy repository."""
    import shutil

    dest = tmp_path / "toy_repo"
    shutil.copytree(TOY_REPO, dest)
    git_init_repo(dest)
    return dest
