"""Unit tests for the Generate -> Validate -> Repair -> Validate -> Fail loop."""

from __future__ import annotations

import pytest

from liedetector.llm import LLMError, generate_validated, load_prompt
from liedetector.models import EXTRACTION_SCHEMA

from .conftest import FakeLLM, extraction_response


def test_valid_first_response_no_repair() -> None:
    llm = FakeLLM([extraction_response([{"quote": "q", "claim_type": "deterministic",
                                         "hypothesis": "h"}])])
    result = generate_validated(llm, "sys", "user", EXTRACTION_SCHEMA)
    assert len(result["claims"]) == 1
    assert len(llm.calls) == 1  # no repair issued


def test_invalid_then_repaired() -> None:
    good = extraction_response([{"quote": "q", "claim_type": "deterministic", "hypothesis": "h"}])
    llm = FakeLLM(['{"claims": "not-a-list"}', good])
    result = generate_validated(llm, "sys", "user", EXTRACTION_SCHEMA)
    assert len(result["claims"]) == 1
    assert len(llm.calls) == 2  # exactly one repair
    assert "failed schema validation" in llm.calls[1][1]


def test_repair_failure_raises_structured_error() -> None:
    llm = FakeLLM(['{"claims": 1}', '{"still": "wrong"}'])
    with pytest.raises(LLMError, match="after repair"):
        generate_validated(llm, "sys", "user", EXTRACTION_SCHEMA)
    assert len(llm.calls) == 2  # only one repair attempt, never a retry loop


def test_non_json_first_response_triggers_repair() -> None:
    good = extraction_response([{"quote": "q", "claim_type": "deterministic", "hypothesis": "h"}])
    llm = FakeLLM(["not json at all", good])
    result = generate_validated(llm, "sys", "user", EXTRACTION_SCHEMA)
    assert len(result["claims"]) == 1


def test_prompt_templates_are_bundled() -> None:
    assert "UNTRUSTED DATA" in load_prompt("extract-v1")
    assert "test_control" in load_prompt("harness-v1")
