"""Unit tests for harness static validation and synthesis repair loop."""

from __future__ import annotations

import pytest

from liedetector.llm import LLMError
from liedetector.models import Claim, ClaimType, Source
from liedetector.synthesize import synthesize_harness, validate_harness_code

from .conftest import UNSAFE_HARNESS, FakeLLM, harness_response

GOOD = '''"""Verifies: the package imports."""


def test_control():
    import toylib  # control assertion
    assert toylib is not None


def test_claim():
    import toylib
    assert toylib.add(2, 3) == 5
'''


def _claim() -> Claim:
    return Claim(
        id="clm-x",
        source=Source(file="README.md", line=1, quote="q"),
        claim_type=ClaimType.DETERMINISTIC,
        hypothesis="toylib.add(2, 3) == 5",
        interpretation_notes="n",
        confidence="high",
    )


def test_good_harness_has_no_errors() -> None:
    assert validate_harness_code(GOOD) == []


def test_syntax_error_detected() -> None:
    assert validate_harness_code("def test_control(: pass")


def test_missing_control_detected() -> None:
    code = "def test_claim():\n    assert True\n"
    errors = validate_harness_code(code)
    assert any("test_control" in e for e in errors)


@pytest.mark.parametrize(
    "snippet",
    [
        "import socket",
        "import subprocess",
        "from urllib import request",
        "import ctypes",
    ],
)
def test_forbidden_imports_detected(snippet: str) -> None:
    code = (
        f"{snippet}\n\ndef test_control():\n    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    assert True\n"
    )
    assert any("forbidden import" in e for e in validate_harness_code(code))


def test_forbidden_os_call_detected() -> None:
    code = (
        "import os\n\ndef test_control():\n    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    os.system('rm -rf /')\n"
    )
    assert any("forbidden call" in e for e in validate_harness_code(code))


def test_forbidden_eval_detected() -> None:
    code = (
        "def test_control():\n    import toylib\n    assert toylib\n\n"
        "def test_claim():\n    eval('1+1')\n"
    )
    assert any("forbidden builtin" in e for e in validate_harness_code(code))


def test_synthesize_returns_valid_harness() -> None:
    llm = FakeLLM([harness_response(GOOD)])
    code = synthesize_harness(llm, _claim(), "toylib")
    assert "test_control" in code
    assert len(llm.calls) == 1


def test_synthesize_repairs_unsafe_harness() -> None:
    unsafe = UNSAFE_HARNESS.format(package="toylib")
    llm = FakeLLM([harness_response(unsafe), harness_response(GOOD)])
    code = synthesize_harness(llm, _claim(), "toylib")
    assert "socket" not in code
    assert len(llm.calls) == 2


def test_synthesize_fails_when_repair_still_unsafe() -> None:
    unsafe = UNSAFE_HARNESS.format(package="toylib")
    llm = FakeLLM([harness_response(unsafe), harness_response(unsafe)])
    with pytest.raises(LLMError, match="after repair"):
        synthesize_harness(llm, _claim(), "toylib")
