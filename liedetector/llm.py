"""Deterministic LLM access with the Generate -> Validate -> Repair -> Fail loop.

All model calls use fixed, versioned prompt templates and a fixed output
schema that is validated on every response.  On validation failure exactly one
targeted repair prompt (containing the validation errors) is issued; if the
repaired response still fails validation the call fails gracefully with a
structured error.  Malformed model output is never executed.

Note on determinism: the directive asks for ``temperature = 0``.  Current
Claude models (Opus 4.8) reject sampling parameters entirely; determinism is
instead approached with versioned prompts, schema-constrained structured
outputs, and validation.  See CODEX_LOG.md.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from typing import Any, Protocol

from .models import SchemaValidationError, validate_schema
from .utils import LieDetectorError

log = logging.getLogger("liedetector.llm")

DEFAULT_MODEL = "claude-opus-4-8"

REPAIR_INSTRUCTION = (
    "Your previous response failed schema validation with these errors:\n"
    "{errors}\n"
    "Return a corrected JSON object that fixes exactly these errors. "
    "Do not change anything else. Respond with JSON only."
)


class LLMError(LieDetectorError):
    """A model call could not produce schema-valid output after one repair."""


class LLMClient(Protocol):
    """Minimal interface every model backend implements."""

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> str:
        """Return the raw text of a single model response."""
        ...


def load_prompt(name: str) -> str:
    """Load a versioned prompt template bundled with the package.

    ``name`` is the versioned prompt id, e.g. ``extract-v1``.
    """
    ref = resources.files("liedetector").joinpath("prompts", f"{name}.txt")
    return ref.read_text(encoding="utf-8")


class AnthropicClient:
    """Claude-backed client using schema-constrained structured outputs."""

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 16000) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        for block in response.content:
            if block.type == "text":
                return str(block.text)
        raise LLMError(f"model returned no text block (stop_reason={response.stop_reason})")


def _parse_and_validate(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError([f"response is not valid JSON: {exc}"]) from exc
    validate_schema(parsed, schema)
    if not isinstance(parsed, dict):
        raise SchemaValidationError(["top-level JSON value must be an object"])
    return parsed


def generate_validated(
    client: LLMClient,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Run the Generate -> Validate -> Repair -> Validate -> Fail loop.

    Returns the schema-valid parsed object, or raises :class:`LLMError` with a
    structured message if one targeted repair attempt also fails validation.
    """
    raw = client.complete(system, user, schema)
    try:
        return _parse_and_validate(raw, schema)
    except SchemaValidationError as first:
        log.warning(
            "model output failed validation; issuing one repair prompt",
            extra={"data": {"errors": first.errors}},
        )
        repair_user = (
            user
            + "\n\n"
            + REPAIR_INSTRUCTION.format(errors="\n".join(f"- {e}" for e in first.errors))
        )
        repaired = client.complete(system, repair_user, schema)
        try:
            return _parse_and_validate(repaired, schema)
        except SchemaValidationError as second:
            raise LLMError(
                "model output failed validation after repair: "
                + "; ".join(second.errors)
            ) from second
