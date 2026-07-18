"""Typed data structures and validation for every pipeline stage.

All state passed between stages is expressed with the frozen dataclasses and
enums defined here.  Model (LLM) output is validated against explicit JSON
schemas with :func:`validate_schema` before anything downstream may use it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class ClaimType(enum.StrEnum):
    """Classification of a README claim."""

    DETERMINISTIC = "deterministic"
    ENVIRONMENT_BOUND = "environment-bound"
    BEHAVIORAL_PROXY = "behavioral-proxy"
    ASPIRATIONAL = "aspirational"

    @property
    def executable(self) -> bool:
        """Only deterministic and environment-bound claims are ever executed."""
        return self in (ClaimType.DETERMINISTIC, ClaimType.ENVIRONMENT_BOUND)


class Verdict(enum.StrEnum):
    """Final verdict for a claim.  There are exactly four; no partial verdicts."""

    PROVEN = "PROVEN"
    FALSE = "FALSE"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNTESTABLE = "UNTESTABLE"


class FailureCategory(enum.StrEnum):
    """Structured failure taxonomy.  Failures are never collapsed generically."""

    INSTALL_FAILURE = "INSTALL_FAILURE"
    IMPORT_FAILURE = "IMPORT_FAILURE"
    TARGET_FAILURE = "TARGET_FAILURE"
    HARNESS_FAILURE = "HARNESS_FAILURE"
    TIMEOUT = "TIMEOUT"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    UNKNOWN = "UNKNOWN"


class Confidence(enum.StrEnum):
    """Evidence-derived confidence in a verdict."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class Source:
    """Where a claim came from.  ``line`` is 1-based and located by exact
    string match against the README, never trusted from the model."""

    file: str
    line: int
    quote: str

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "quote": self.quote}


@dataclass(frozen=True)
class Claim:
    """A single validated factual assertion extracted from the README."""

    id: str
    source: Source
    claim_type: ClaimType
    hypothesis: str
    interpretation_notes: str
    confidence: str
    status: str = "pending"
    suggested_strategy: str = ""

    def record(self) -> dict[str, Any]:
        """The canonical claim record that is hashed into the receipt."""
        return {
            "id": self.id,
            "source": self.source.to_dict(),
            "claim_type": self.claim_type.value,
            "hypothesis": self.hypothesis,
            "interpretation_notes": self.interpretation_notes,
            "confidence": self.confidence,
            "suggested_strategy": self.suggested_strategy,
        }


@dataclass(frozen=True)
class ExecutionRun:
    """One containerised execution of one harness."""

    run_index: int
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    control_passed: bool | None
    claim_passed: bool | None

    @property
    def passed(self) -> bool:
        return (
            not self.timed_out
            and self.exit_code == 0
            and self.control_passed is True
            and self.claim_passed is True
        )

    def log_text(self) -> str:
        """Combined evidence log for this run (hashed into the receipt)."""
        return (
            f"# run {self.run_index}\n"
            f"# exit_code={self.exit_code} timed_out={self.timed_out}\n"
            f"## stdout\n{self.stdout}\n## stderr\n{self.stderr}\n"
        )


@dataclass(frozen=True)
class InstallResult:
    """Result of the single dependency-installation phase."""

    ok: bool
    exit_code: int
    log: str


@dataclass
class Evaluation:
    """Everything the pipeline knows about one claim after adjudication."""

    claim: Claim
    harness_code: str | None = None
    harness_error: str | None = None
    runs: list[ExecutionRun] = field(default_factory=list)
    verdict: Verdict = Verdict.INCONCLUSIVE
    failure_category: FailureCategory | None = None
    verdict_confidence: Confidence = Confidence.LOW
    rationale: str = ""


class SchemaValidationError(Exception):
    """Raised when a model response does not match its output schema."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _validate(instance: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if expected is not None:
        py_type = _TYPE_MAP[expected]
        if isinstance(instance, bool) and expected in ("integer", "number"):
            errors.append(f"{path}: expected {expected}, got boolean")
            return
        if not isinstance(instance, py_type):
            errors.append(f"{path}: expected {expected}, got {type(instance).__name__}")
            return
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")
    if expected == "object":
        props: dict[str, Any] = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required key {key!r}")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: unexpected key {key!r}")
        for key, sub in props.items():
            if key in instance:
                _validate(instance[key], sub, f"{path}.{key}", errors)
    elif expected == "array" and "items" in schema:
        for i, item in enumerate(instance):
            _validate(item, schema["items"], f"{path}[{i}]", errors)


def validate_schema(instance: Any, schema: dict[str, Any]) -> None:
    """Validate ``instance`` against a JSON-schema subset.

    Supports the subset used by this tool's output schemas: ``type``,
    ``properties``, ``required``, ``additionalProperties: false``, ``items``
    and ``enum``.  Raises :class:`SchemaValidationError` with every error found.
    """
    errors: list[str] = []
    _validate(instance, schema, "$", errors)
    if errors:
        raise SchemaValidationError(errors)


#: Output schema for the claim-extraction model call (prompt ``extract-v1``).
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "claim_type": {
                        "type": "string",
                        "enum": [t.value for t in ClaimType],
                    },
                    "hypothesis": {"type": "string"},
                    "interpretation_notes": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "suggested_strategy": {"type": "string"},
                },
                "required": [
                    "quote",
                    "claim_type",
                    "hypothesis",
                    "interpretation_notes",
                    "confidence",
                    "suggested_strategy",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

#: Output schema for the harness-synthesis model call (prompt ``harness-v1``).
HARNESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "harness_code": {"type": "string"},
    },
    "required": ["harness_code"],
    "additionalProperties": False,
}
