"""Unit tests for the conservative adjudicator and failure taxonomy."""

from __future__ import annotations

from liedetector.adjudicate import adjudicate, adjudicate_install_failure
from liedetector.models import (
    Claim,
    ClaimType,
    Confidence,
    FailureCategory,
    Source,
    Verdict,
)

from .conftest import make_run

HARNESS = "def test_control():\n    pass\n"


def _claim() -> Claim:
    return Claim(
        id="clm-x",
        source=Source(file="README.md", line=1, quote="q"),
        claim_type=ClaimType.DETERMINISTIC,
        hypothesis="h",
        interpretation_notes="n",
        confidence="high",
    )


def test_pass_pass_is_proven_high_confidence() -> None:
    runs = [make_run(1), make_run(2)]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict == Verdict.PROVEN
    assert ev.verdict_confidence == Confidence.HIGH


def test_pass_fail_is_inconclusive() -> None:
    runs = [make_run(1, claim_passed=True), make_run(2, exit_code=1, claim_passed=False)]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict == Verdict.INCONCLUSIVE


def test_fail_fail_in_target_is_false() -> None:
    tb = 'File "/repo/toylib/__init__.py", line 3\nAssertionError'
    runs = [
        make_run(1, exit_code=1, stdout=tb, control_passed=True, claim_passed=False),
        make_run(2, exit_code=1, stdout=tb, control_passed=True, claim_passed=False),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict == Verdict.FALSE
    assert ev.failure_category == FailureCategory.TARGET_FAILURE
    assert ev.verdict_confidence == Confidence.HIGH


def test_fail_fail_with_failed_control_is_inconclusive_never_false() -> None:
    runs = [
        make_run(1, exit_code=1, control_passed=False, claim_passed=None),
        make_run(2, exit_code=1, control_passed=False, claim_passed=None),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict == Verdict.INCONCLUSIVE
    assert ev.verdict != Verdict.FALSE


def test_timeout_is_inconclusive_never_false() -> None:
    runs = [
        make_run(1, timed_out=True, control_passed=None, claim_passed=None),
        make_run(2, timed_out=True, control_passed=None, claim_passed=None),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.verdict == Verdict.INCONCLUSIVE
    assert ev.failure_category == FailureCategory.TIMEOUT


def test_import_failure_categorised() -> None:
    tb = "ModuleNotFoundError: No module named 'toylib'"
    runs = [
        make_run(1, exit_code=1, stdout=tb, control_passed=False, claim_passed=None),
        make_run(2, exit_code=1, stdout=tb, control_passed=False, claim_passed=None),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.failure_category == FailureCategory.IMPORT_FAILURE
    assert ev.verdict == Verdict.INCONCLUSIVE


def test_resource_limit_categorised() -> None:
    runs = [
        make_run(1, exit_code=137, control_passed=True, claim_passed=False, stderr="Killed"),
        make_run(2, exit_code=137, control_passed=True, claim_passed=False, stderr="Killed"),
    ]
    ev = adjudicate(_claim(), HARNESS, runs, "toylib")
    assert ev.failure_category == FailureCategory.RESOURCE_LIMIT
    assert ev.verdict == Verdict.INCONCLUSIVE


def test_install_failure_is_inconclusive() -> None:
    ev = adjudicate_install_failure(_claim())
    assert ev.verdict == Verdict.INCONCLUSIVE
    assert ev.failure_category == FailureCategory.INSTALL_FAILURE
