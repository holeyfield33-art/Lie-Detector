"""Unit tests for the schema validator and core data structures."""

from __future__ import annotations

import pytest

from liedetector.models import (
    EXTRACTION_SCHEMA,
    HARNESS_SCHEMA,
    ClaimType,
    SchemaValidationError,
    validate_schema,
)


def _claim(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "quote": "q",
        "claim_type": "deterministic",
        "hypothesis": "h",
        "interpretation_notes": "n",
        "confidence": "high",
        "suggested_strategy": "",
    }
    base.update(overrides)
    return base


def test_valid_extraction_payload_passes() -> None:
    validate_schema({"claims": [_claim()]}, EXTRACTION_SCHEMA)


def test_missing_required_key_fails() -> None:
    claim = _claim()
    del claim["hypothesis"]
    with pytest.raises(SchemaValidationError, match="hypothesis"):
        validate_schema({"claims": [claim]}, EXTRACTION_SCHEMA)


def test_bad_enum_fails() -> None:
    with pytest.raises(SchemaValidationError, match="claim_type"):
        validate_schema({"claims": [_claim(claim_type="magic")]}, EXTRACTION_SCHEMA)


def test_additional_property_fails() -> None:
    with pytest.raises(SchemaValidationError, match="unexpected key"):
        validate_schema({"claims": [_claim(extra=1)]}, EXTRACTION_SCHEMA)


def test_wrong_type_fails() -> None:
    with pytest.raises(SchemaValidationError, match="expected array"):
        validate_schema({"claims": "not-a-list"}, EXTRACTION_SCHEMA)


def test_boolean_is_not_integer() -> None:
    with pytest.raises(SchemaValidationError):
        validate_schema(True, {"type": "integer"})


def test_multiple_errors_reported() -> None:
    claim = _claim(claim_type="nope")
    del claim["quote"]
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_schema({"claims": [claim]}, EXTRACTION_SCHEMA)
    assert len(excinfo.value.errors) == 2


def test_harness_schema() -> None:
    validate_schema({"harness_code": "def test_control(): pass"}, HARNESS_SCHEMA)
    with pytest.raises(SchemaValidationError):
        validate_schema({}, HARNESS_SCHEMA)


def test_claim_type_executability() -> None:
    assert ClaimType.DETERMINISTIC.executable
    assert ClaimType.ENVIRONMENT_BOUND.executable
    assert not ClaimType.BEHAVIORAL_PROXY.executable
    assert not ClaimType.ASPIRATIONAL.executable
