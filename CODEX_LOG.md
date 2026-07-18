# CODEX_LOG

Engineering decisions and deviations, per the MVP v1.2 directive's final
decision rule (prefer the more deterministic, auditable, explainable, smaller,
more maintainable option).

## Determinism vs. `temperature = 0`

The directive asks for `temperature = 0` on all model calls. The current Claude
models (Opus 4.8, the default here) **reject sampling parameters entirely** — a
`temperature` field returns HTTP 400. Determinism is therefore approached the
way the directive's own LLM Determinism section prioritises: fixed, versioned
system prompts (`extract-v1`, `harness-v1`), a fixed output schema validated on
every response (schema-constrained structured outputs), and location of claims
by exact string match rather than model-reported positions. Prompt versions are
recorded in the receipt. This is strictly more auditable than a sampling knob
and does not depend on a parameter the API no longer accepts.

## Docker image pinned by digest

`python:3.12-slim` is pinned by digest (`sha256:57cd7c3a...`), resolved with
`docker buildx imagetools inspect`, not by tag. The digest is recorded in the
receipt (`docker_image_digest`) and in the environment fingerprint.

## Receipt as the canonical root of trust

Restructured per the v1.2 review to remove circularity: inputs and evidence
(README, claim records, harnesses, execution logs) are hashed into the receipt;
the receipt is canonicalised and hashed into the sidecar
`verification_receipt.sha256`; the HTML report embeds the receipt hash in its
footer. The report is a derived view and is **not** hashed into the receipt.

The receipt bundle is self-contained: every hashed artifact is written to disk
next to the receipt with paths relative to it, so `liedetector verify` validates
the whole hash chain offline.

## Generate -> Validate -> Repair -> Validate -> Fail

Implemented in `llm.generate_validated`. Exactly one targeted repair prompt
(carrying the validation errors) is issued on failure; a second failure fails
the claim gracefully with a structured error. Harness synthesis extends the same
loop with a static safety scan (`synthesize.validate_harness_code`). There is no
blind regeneration and no retry loop.

## Four verdicts, no partial category; immutable workspace, no rollback

Per the v1.2 rejections: exactly four verdicts (`PROVEN`, `FALSE`,
`INCONCLUSIVE`, `UNTESTABLE`); one execution path (no demo mode — `liedetector
demo` reuses the identical pipeline against a local toy repo); the workspace is
treated as immutable input and removed on exit, with no rollback logic.

## Correctness fixes found by running the real sandbox

Exercising the pipeline against a live Docker daemon surfaced two real bugs,
both fixed:

1. **In-tree build vs. read-only mount.** `pip install /repo` runs the build
   backend's `egg_info` step inside the source tree, which fails against the
   required read-only repository mount (`Read-only file system`). Fixed by
   copying the repo into the container's writable `tmpfs` and building from the
   copy, so the immutable-input invariant holds.
2. **Sandbox user cannot read root-owned staging dirs.** The non-root sandbox
   user (`1000:1000`) could not traverse the harness/venv staging directories,
   which `tempfile` creates mode `0700` owned by root (`file or directory not
   found`). Fixed by making the venv volume and harness staging world-readable
   before mounting.

## Proxied-environment accommodation (opt-in)

For environments behind a TLS-terminating egress proxy (including this build
environment), the **install phase only** honours `HTTP(S)_PROXY`,
`LIEDETECTOR_CA_BUNDLE`, and `LIEDETECTOR_INSTALL_HOST_NETWORK=1`. The execution
phase is never affected and always runs with `--network none`. Off by default.

## Testing

The hermetic suite (88 tests) drives the entire pipeline with a scripted
`FakeLLM` and a `FakeExecutor`, so it runs without Docker or an API credential
and is byte-stable (a golden reproducibility test asserts byte-identical receipt
bytes across two runs; a tamper test asserts `verify` fails when an artifact is
edited). The opt-in `tests/test_e2e_demo.py` (`-m docker`) runs the real
container end-to-end and confirms `add(2,3)==5` is `PROVEN` while the
deliberately buggy `count_words("a  b")==2` is `FALSE`.

Quality gates at completion: `ruff check` clean, `mypy --strict` clean (13
source files), 88 hermetic tests passing, real-sandbox E2E passing.
