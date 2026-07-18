"""Unit tests for claim extraction: line location, stable IDs, ordering."""

from __future__ import annotations

from liedetector.extract import claim_id, extract_claims, locate_quote

from .conftest import FakeLLM, extraction_response

README = "line one\nthe answer is 42\nrepeat\nrepeat\n"


def test_locate_quote_finds_line() -> None:
    used: dict[str, int] = {}
    assert locate_quote(README, "the answer is 42", used) == (2, 0)


def test_locate_quote_handles_repeats() -> None:
    used: dict[str, int] = {}
    assert locate_quote(README, "repeat", used) == (3, 0)
    assert locate_quote(README, "repeat", used) == (4, 1)


def test_locate_missing_quote_returns_none() -> None:
    assert locate_quote(README, "absent", {}) is None


def test_claim_ids_are_stable_and_deterministic() -> None:
    assert claim_id("q", 0) == claim_id("q", 0)
    assert claim_id("q", 0) != claim_id("q", 1)
    assert claim_id("q", 0).startswith("clm-")


def test_extract_drops_quotes_not_in_readme() -> None:
    llm = FakeLLM(
        [
            extraction_response(
                [
                    {"quote": "the answer is 42", "claim_type": "deterministic",
                     "hypothesis": "returns 42"},
                    {"quote": "hallucinated quote", "claim_type": "deterministic",
                     "hypothesis": "nope"},
                ]
            )
        ]
    )
    claims = extract_claims(llm, README)
    assert len(claims) == 1
    assert claims[0].source.line == 2


def test_extract_orders_by_readme_position() -> None:
    llm = FakeLLM(
        [
            extraction_response(
                [
                    {"quote": "repeat", "claim_type": "deterministic", "hypothesis": "b"},
                    {"quote": "line one", "claim_type": "deterministic", "hypothesis": "a"},
                ]
            )
        ]
    )
    claims = extract_claims(llm, README)
    assert [c.source.line for c in claims] == [1, 3]


def test_extract_is_reproducible() -> None:
    payload = extraction_response(
        [{"quote": "the answer is 42", "claim_type": "deterministic", "hypothesis": "h"}]
    )
    first = extract_claims(FakeLLM([payload]), README)
    second = extract_claims(FakeLLM([payload]), README)
    assert [c.id for c in first] == [c.id for c in second]
