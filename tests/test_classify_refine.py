"""Unit tests for classification and refinement invariants."""

from __future__ import annotations

from liedetector.classify import classify
from liedetector.models import Claim, ClaimType, Source, Verdict
from liedetector.refine import refine


def _claim(claim_type: ClaimType, hypothesis: str = "h", strategy: str = "") -> Claim:
    return Claim(
        id=f"clm-{claim_type.value}",
        source=Source(file="README.md", line=1, quote="q"),
        claim_type=claim_type,
        hypothesis=hypothesis,
        interpretation_notes="n",
        confidence="high",
        suggested_strategy=strategy,
    )


def test_classify_splits_executable_from_untestable() -> None:
    claims = [
        _claim(ClaimType.DETERMINISTIC),
        _claim(ClaimType.ENVIRONMENT_BOUND),
        _claim(ClaimType.BEHAVIORAL_PROXY),
        _claim(ClaimType.ASPIRATIONAL),
    ]
    executable, untestable = classify(claims)
    assert len(executable) == 2
    assert len(untestable) == 2
    assert all(e.verdict == Verdict.UNTESTABLE for e in untestable)


def test_untestable_carries_a_strategy() -> None:
    _, untestable = classify([_claim(ClaimType.BEHAVIORAL_PROXY)])
    assert untestable[0].claim.suggested_strategy
    assert untestable[0].claim.status == "untestable"


def test_untestable_uses_model_strategy_when_present() -> None:
    _, untestable = classify([_claim(ClaimType.ASPIRATIONAL, strategy="check the roadmap file")])
    assert untestable[0].claim.suggested_strategy == "check the roadmap file"


def test_refine_passes_valid_hypotheses() -> None:
    refined, failed = refine([_claim(ClaimType.DETERMINISTIC, hypothesis="precise")])
    assert len(refined) == 1
    assert not failed


def test_refine_fails_empty_hypothesis_gracefully() -> None:
    refined, failed = refine([_claim(ClaimType.DETERMINISTIC, hypothesis="   ")])
    assert not refined
    assert len(failed) == 1
    assert failed[0].verdict.value == "INCONCLUSIVE"
