"""Adjudication: conservative mapping from execution evidence to verdicts.

Double-execution policy (no exceptions):

- PASS PASS -> PROVEN
- FAIL FAIL -> continue to adjudication (FALSE only under strict conditions)
- PASS FAIL -> INCONCLUSIVE

``FALSE`` requires ALL of: both executions failed, the control assertion
passed, the traceback originates in the target package, the harness is not
responsible, and the environment is healthy.  Anything else is INCONCLUSIVE.
Confidence is evidence-derived, never invented.
"""

from __future__ import annotations

import logging
import re

from .models import (
    Claim,
    Confidence,
    Evaluation,
    ExecutionRun,
    FailureCategory,
    Verdict,
)

log = logging.getLogger("liedetector.adjudicate")

_RESOURCE_SIGNS = ("MemoryError", "Killed", "OOM", "Cannot allocate memory")
_IMPORT_SIGNS = ("ModuleNotFoundError", "ImportError")


def _identical(a: ExecutionRun, b: ExecutionRun) -> bool:
    """Same observable outcome across both runs (timing noise ignored)."""
    return (
        a.exit_code == b.exit_code
        and a.timed_out == b.timed_out
        and a.control_passed == b.control_passed
        and a.claim_passed == b.claim_passed
    )


def _traceback_in_target(run: ExecutionRun, package: str) -> bool:
    """Does the failure traceback originate inside the target package?"""
    text = run.stdout + "\n" + run.stderr
    pattern = re.compile(
        r"File \"[^\"]*(?:site-packages[/\\]" + re.escape(package) + r"|/repo)[^\"]*\""
    )
    return bool(pattern.search(text))


def _classify_failure(run: ExecutionRun, package: str) -> FailureCategory:
    text = run.stdout + "\n" + run.stderr
    if run.timed_out:
        return FailureCategory.TIMEOUT
    if any(sign in text for sign in _RESOURCE_SIGNS) or run.exit_code == 137:
        return FailureCategory.RESOURCE_LIMIT
    if run.control_passed is not True and any(sign in text for sign in _IMPORT_SIGNS):
        return FailureCategory.IMPORT_FAILURE
    if run.control_passed is not True:
        return FailureCategory.HARNESS_FAILURE
    if _traceback_in_target(run, package):
        return FailureCategory.TARGET_FAILURE
    if run.claim_passed is False:
        # test_claim failed on a plain assertion about the target's behaviour
        return FailureCategory.TARGET_FAILURE
    return FailureCategory.UNKNOWN


def adjudicate_install_failure(claim: Claim) -> Evaluation:
    """Environment never became healthy: every executable claim is INCONCLUSIVE."""
    return Evaluation(
        claim=claim,
        verdict=Verdict.INCONCLUSIVE,
        failure_category=FailureCategory.INSTALL_FAILURE,
        verdict_confidence=Confidence.LOW,
        rationale="Dependency installation failed; the claim was never executed.",
    )


def adjudicate_harness_failure(claim: Claim, error: str) -> Evaluation:
    """The model could not produce a valid harness: fail gracefully."""
    return Evaluation(
        claim=claim,
        harness_error=error,
        verdict=Verdict.INCONCLUSIVE,
        failure_category=FailureCategory.HARNESS_FAILURE,
        verdict_confidence=Confidence.LOW,
        rationale="No valid harness could be generated; malformed model output is never executed.",
    )


def adjudicate(
    claim: Claim,
    harness_code: str,
    runs: list[ExecutionRun],
    package: str,
) -> Evaluation:
    """Map one claim's two execution runs to a verdict.  Judge conservatively."""
    if len(runs) != 2:
        raise ValueError("double execution requires exactly two runs")
    first, second = runs
    identical = _identical(first, second)

    evaluation = Evaluation(claim=claim, harness_code=harness_code, runs=runs)

    if first.timed_out or second.timed_out:
        evaluation.verdict = Verdict.INCONCLUSIVE
        evaluation.failure_category = FailureCategory.TIMEOUT
        evaluation.verdict_confidence = Confidence.LOW
        evaluation.rationale = "Execution hit the hard 120s timeout; timeouts are never FALSE."
        return evaluation

    if first.passed and second.passed:
        evaluation.verdict = Verdict.PROVEN
        evaluation.verdict_confidence = Confidence.HIGH if identical else Confidence.MEDIUM
        evaluation.rationale = "Both executions passed, including the control assertion."
        return evaluation

    if first.passed != second.passed:
        evaluation.verdict = Verdict.INCONCLUSIVE
        evaluation.failure_category = FailureCategory.UNKNOWN
        evaluation.verdict_confidence = Confidence.MEDIUM
        evaluation.rationale = "Executions disagreed (PASS/FAIL): nondeterministic evidence."
        return evaluation

    # Both runs failed: FALSE requires every strict condition to hold.
    category = _classify_failure(first, package)
    control_ok = first.control_passed is True and second.control_passed is True

    if not control_ok:
        evaluation.verdict = Verdict.INCONCLUSIVE
        evaluation.failure_category = category
        evaluation.verdict_confidence = Confidence.LOW
        evaluation.rationale = (
            "The control assertion failed, so the environment or harness is at "
            "fault; a failed control is never FALSE."
        )
        return evaluation

    if category == FailureCategory.TARGET_FAILURE:
        evaluation.verdict = Verdict.FALSE
        evaluation.failure_category = category
        evaluation.verdict_confidence = Confidence.HIGH if identical else Confidence.MEDIUM
        evaluation.rationale = (
            "Both executions failed, the control assertion passed, and the "
            "failure originates in the target package."
        )
        return evaluation

    evaluation.verdict = Verdict.INCONCLUSIVE
    evaluation.failure_category = category
    evaluation.verdict_confidence = Confidence.LOW
    evaluation.rationale = (
        f"Both executions failed but the failure ({category.value}) cannot be "
        "attributed to the target package with confidence."
    )
    return evaluation
