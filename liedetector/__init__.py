"""The Lie Detector: verify factual claims in a repository's README.

The package implements a deterministic pipeline:

    Clone -> Freeze Commit -> Extract Claims -> Classify -> Refine ->
    Generate Harness -> Execute (x2) -> Adjudicate ->
    verification_receipt.json -> Truth Report (HTML)

The canonical artifact is ``verification_receipt.json``; the HTML Truth
Report is a derived view rendered from the receipt plus stored logs.
"""

TOOL_VERSION = "0.1.0"
SCHEMA_VERSION = "1.0"
RECEIPT_VERSION = "1"

PROMPT_VERSIONS = {
    "extraction": "extract-v1",
    "harness": "harness-v1",
}

#: Execution sandbox image, pinned by digest (never by tag).
DOCKER_IMAGE = (
    "python:3.12-slim"
    "@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)

#: Default model for the OpenAI-compatible provider (used with --provider openai).
DEFAULT_OPENAI_MODEL = "gpt-4o"
