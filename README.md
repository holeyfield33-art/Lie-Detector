# The Lie Detector

**Turn READMEs into tests.**

The Lie Detector is a deterministic CLI tool that verifies factual claims in a
repository's README by generating executable verification harnesses and
producing an evidence-backed **Truth Report**, backed by an immutable
**verification receipt**.

```bash
liedetector run https://github.com/user/repo
```

You get a polished, reproducible Truth Report (`reports/<commit>.html`) plus a
canonical `verification_receipt.json` whose SHA-256 is printed to the console.

## What it does

Every factual claim in a README ("`add(2, 3)` returns `5`", "handles Unicode",
"installs with pip") is extracted, classified, refined into a precise testable
hypothesis, turned into exactly one sandboxed pytest harness, executed **twice**
in a locked-down container, and adjudicated to one of four verdicts:

| Verdict | Meaning |
| --- | --- |
| `PROVEN` | Both executions passed, including a trivial control assertion. |
| `FALSE` | Both executions failed, the control passed, and the failure originates in the target package. Judged conservatively. |
| `INCONCLUSIVE` | Ambiguous evidence: disagreeing runs, timeouts, install/import/harness failures, or a failed control. Never mislabelled `FALSE`. |
| `UNTESTABLE` | Behavioral-proxy or aspirational claims, displayed with a suggested verification strategy but never executed. |

The original quote and the interpreted hypothesis are always shown together —
the interpretation is never hidden.

## Pipeline

```
Clone -> Freeze Commit -> Extract Claims -> Classify -> Refine ->
Generate Harness -> Execute (x2) -> Adjudicate ->
verification_receipt.json -> Truth Report (HTML)
```

Each stage is independently testable and passes validated, typed data
structures — no global mutable state. The commit SHA is frozen immediately
after checkout and is the identity of the analysis.

## Determinism & trust

- **Versioned prompts** (`extract-v1`, `harness-v1`) and schema-validated
  structured outputs; prompt versions are recorded in the receipt. Rerunning
  against the same commit with the same tool version produces the same claims.
- **Repair, not retry**: a model response that fails schema validation gets one
  targeted repair prompt; if that also fails, the claim fails gracefully.
  Malformed model output is never executed.
- **The receipt is the root of trust.** Inputs and evidence (README, each claim
  record, each harness, each execution log) are SHA-256 hashed into the
  canonical `verification_receipt.json`, which is itself hashed into a sidecar
  and embedded in the report footer. The report is a derived view; the receipt
  never references the report.

See [`SECURITY.md`](SECURITY.md) for the full sandbox and prompt-safety model.

## CLI

```
liedetector run <repo_url>    # full pipeline against a GitHub repository
liedetector verify <receipt>  # recompute hashes, validate receipt integrity
liedetector demo              # run against the bundled toy repo
liedetector doctor            # check Docker, Python, provider credentials
liedetector version
```

`run` and `demo` both accept `--provider {anthropic,openai}`, `--model`, and
`--base-url` (see [LLM providers](#llm-providers)); `doctor` reports the
status of both providers so you can see which one is ready to use.

`liedetector verify` recomputes every artifact hash and the receipt hash from
stored artifacts and confirms they match — tamper with any artifact and it
fails.

## Requirements

- Python 3.11+
- Docker (image is pinned by digest; `python:3.12-slim@sha256:...`)
- An LLM credential for one of the two supported providers:
  - **Anthropic** (default): `ANTHROPIC_API_KEY`, or a stored `ant auth login`
    profile.
  - **OpenAI-compatible** (OpenAI, Featherless, OpenRouter, local llama.cpp,
    etc.): `OPENAI_API_KEY` or `FEATHERLESS_API_KEY`, plus `OPENAI_BASE_URL`
    pointing at the provider's endpoint. Select it with `--provider openai`.

See [RUN_GUIDE.md](RUN_GUIDE.md) for full setup instructions, including
Docker Desktop/WSL2 on Windows and Featherless configuration.

## Install

```bash
pip install -e ".[dev,openai]"   # omit ",openai" if you only use Anthropic
liedetector doctor                # verify your environment
```

Copy [`.env.example`](.env.example) to `.env` and fill in your credentials;
`liedetector` loads `.env` from the current directory automatically (without
overriding variables already set in your shell). `.env` is git-ignored and
never committed — see [RUN_GUIDE.md](RUN_GUIDE.md#credentials) before editing
`.env.example`, which must only ever contain placeholder values.

## LLM providers

Every command that calls a model (`run`, `demo`) accepts:

```
--provider {anthropic,openai}   # default: anthropic
--model <name>                  # override the provider's default model
--base-url <url>                # OpenAI-compatible endpoint, e.g. Featherless
```

```bash
# Anthropic (default)
liedetector demo

# Featherless, via OpenAI-compatible API
liedetector demo --provider openai --base-url https://api.featherless.ai/v1 \
  --model <model-id>
```

`--base-url` can also come from `OPENAI_BASE_URL`. The OpenAI-compatible
client tries schema-constrained `json_schema` output first and transparently
falls back to `json_object` mode with the schema embedded in the prompt if
the provider doesn't support structured outputs — the Generate -> Validate ->
Repair -> Validate -> Fail loop (`llm.py`) is identical for both providers.

## Demo

```bash
make demo
# or, against Featherless:
liedetector demo --provider openai --base-url https://api.featherless.ai/v1
```

The bundled `demo/toy_repo/` has known-true claims (`add`, `slugify`),
a known-false claim (`count_words("a  b")` is documented as `2` but returns
`3`), and untestable claims ("blazing fast", "Rust bindings planned"). A run
produces 3 `PROVEN`, 1 `FALSE`, 2 `UNTESTABLE`.

## Scope

Supported: Python repositories, PyPI-installable packages, `README.md`
ingestion, deterministic and environment-bound claims, a static HTML report, an
immutable JSON receipt, and a Docker execution sandbox. Behavioral-proxy and
aspirational claims are displayed but never executed.

## Development

```bash
make check        # ruff + mypy (strict) + hermetic tests
make test         # hermetic unit/integration tests (no Docker, no API)
make test-docker  # opt-in real-sandbox end-to-end test (requires Docker)
```

The hermetic suite and `test-docker` both pass on Windows. Two host-filesystem
quirks are worked around internally and are worth knowing about if you're
modifying `cli.py`, `utils.py`, or `executor.py`:

- Hashed artifacts (`README.md`, harnesses, logs) are written as raw UTF-8
  bytes, not via text-mode `write_text`, so Windows' newline translation
  (`\n` -> `\r\n`) can never desync the on-disk bytes from the hash the
  receipt committed to.
- The cloned workspace and the Docker executor's venv volume are cleaned up
  defensively: Git marks objects read-only (Windows refuses to delete a
  read-only file regardless of directory permissions) and `python -m venv`
  creates a `lib64 -> lib` POSIX symlink that Windows can't traverse when the
  volume is bind-mounted from a Linux container. Both are cleared/removed
  before the host-side `rmtree` runs.

The hermetic test suite drives the whole pipeline with a scripted model and a
fake sandbox, so it is byte-stable and needs neither Docker nor an API key. The
opt-in `test-docker` target exercises the real container.

## Layout

```
liedetector/
  cli.py  extract.py  classify.py  refine.py  synthesize.py
  executor.py  adjudicate.py  receipt.py  report.py  models.py  utils.py
  llm.py  prompts/            # versioned prompt templates
harnesses/  reports/  receipts/
tests/
demo/toy_repo/
```
