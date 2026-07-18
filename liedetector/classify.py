"""Claim classification: decide which claims are executable.

Classification labels come from the extraction stage; this stage is fully
deterministic.  Only ``deterministic`` and ``environment-bound`` claims are
ever executed.  Everything else becomes ``UNTESTABLE`` immediately, displayed
with a suggested verification strategy.  There is no partial category.
"""

from __future__ import annotations

import logging

from .models import Claim, Confidence, Evaluation, Verdict

log = logging.getLogger("liedetector.classify")

_DEFAULT_STRATEGY = {
    "behavioral-proxy": (
        "Design a benchmark or observational study for this behaviour; a "
        "single harness cannot decide it."
    ),
    "aspirational": (
        "Check the project roadmap, issue tracker or release notes; future "
        "plans cannot be verified by execution."
    ),
}


def classify(claims: list[Claim]) -> tuple[list[Claim], list[Evaluation]]:
    """Split claims into executable claims and pre-adjudicated UNTESTABLE ones."""
    executable: list[Claim] = []
    untestable: list[Evaluation] = []
    for claim in claims:
        if claim.claim_type.executable:
            executable.append(claim)
            continue
        strategy = claim.suggested_strategy or _DEFAULT_STRATEGY.get(
            claim.claim_type.value, "Manual review required."
        )
        evaluation = Evaluation(
            claim=Claim(
                id=claim.id,
                source=claim.source,
                claim_type=claim.claim_type,
                hypothesis=claim.hypothesis,
                interpretation_notes=claim.interpretation_notes,
                confidence=claim.confidence,
                status="untestable",
                suggested_strategy=strategy,
            ),
            verdict=Verdict.UNTESTABLE,
            verdict_confidence=Confidence.HIGH,
            rationale=f"{claim.claim_type.value} claims are displayed but never executed.",
        )
        untestable.append(evaluation)
    log.info(
        "classified claims",
        extra={"data": {"executable": len(executable), "untestable": len(untestable)}},
    )
    return executable, untestable
