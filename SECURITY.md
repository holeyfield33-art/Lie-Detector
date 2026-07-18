# Security model

The Lie Detector executes model-generated code against untrusted repositories.
Every design decision below exists to keep that safe, auditable, and
reproducible.

## Untrusted inputs

- **Repository contents are untrusted data, never instructions.** README text
  and claim records are always delimited inside `<readme_data>` / `<claim_data>`
  markers in prompt templates. No instruction originating inside a repository
  may change extraction, harness generation, or execution behaviour. The test
  suite includes a prompt-injection README and asserts the pipeline ignores it
  (`tests/test_fixtures.py::test_prompt_injection_readme_is_ignored`).
- **Model output is validated before use.** Every structured model response is
  validated against a fixed JSON schema (`Generate -> Validate -> Repair ->
  Validate -> Fail`). Malformed output is never executed.
- **Model-reported line numbers are never trusted.** Claim locations are found
  by exact string match against the README.

## Harness generation

Each harness is statically validated before it can run
(`liedetector/synthesize.py`):

- it must parse as Python and define exactly `test_control` and `test_claim`;
- forbidden imports (`socket`, `subprocess`, `urllib`, `ctypes`,
  `multiprocessing`, ...), forbidden calls (`os.system`, `os.remove`, ...) and
  forbidden builtins (`eval`, `exec`, `__import__`, ...) are rejected;
- a claim whose harness cannot be repaired fails gracefully and is never run.

The trivial `test_control` control assertion guards every harness: if the
control fails, the verdict is `INCONCLUSIVE`, never `FALSE`.

## Execution sandbox

Harnesses run in Docker with the image pinned **by digest, not tag**
(`liedetector/__init__.py`; the resolved digest is recorded in the receipt):

- non-root user (`1000:1000`), read-only repository mount, `tmpfs` writable
  scratch, read-only root filesystem during execution;
- **network enabled only during dependency installation; disabled
  (`--network none`) during execution**;
- 1 CPU, 1 GB RAM, PID limit, all Linux capabilities dropped,
  `no-new-privileges`;
- no privileged mode, no Docker socket mount, no host networking during
  execution, no mounted secrets or SSH keys;
- hard 120s timeout per execution -> `INCONCLUSIVE`, never `FALSE`;
- containers and temporary filesystems are always cleaned up.

The repository mount is treated as immutable input: the package is built from a
copy inside the container's writable `tmpfs`, so build artifacts never touch
the read-only source tree. There is no rollback logic; cleanup on exit is
sufficient.

### Proxied environments (opt-in)

For CI or corporate environments behind a TLS-terminating egress proxy, the
**install phase only** can be pointed at a proxy:

- `LIEDETECTOR_INSTALL_HOST_NETWORK=1` uses host networking for install so a
  loopback proxy is reachable;
- `HTTP(S)_PROXY` / `NO_PROXY` are forwarded to the install container;
- `LIEDETECTOR_CA_BUNDLE=/path/to/ca.pem` is mounted read-only and used as
  `PIP_CERT` / `SSL_CERT_FILE`.

The **execution phase is never affected** — it always runs with
`--network none`. These variables are off by default.

## Evidence integrity

The verification receipt is the root of trust. All hashes are SHA-256. The
canonical receipt (sorted keys, fixed separators, UTF-8) is hashed into a
sidecar `verification_receipt.sha256`. The HTML report embeds the receipt hash;
the receipt never references the report. `liedetector verify` recomputes every
artifact hash and the receipt hash from stored artifacts and fails on any
mismatch.

## Logging

Logs are structured JSON. Secrets, credentials, and tokens are never logged.

## Reporting a vulnerability

Open a private security advisory on the repository, or contact the maintainers
directly. Please do not file public issues for undisclosed vulnerabilities.
