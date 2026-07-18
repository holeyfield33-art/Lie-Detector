"""The verification receipt: canonical JSON root of trust plus hash chain.

Hash chain (all SHA-256):

1. Inputs and evidence are hashed: README, each claim record, each harness,
   each execution log.
2. Those hashes are stored inside ``verification_receipt.json``.
3. The receipt is canonicalised (sorted keys, fixed separators, UTF-8) and
   hashed; the hash is emitted as the sidecar ``verification_receipt.sha256``
   and printed in CLI output.
4. The HTML report embeds the receipt hash in its footer.  The report
   visualises the receipt; the receipt never references the report.

Reproducibility rule: only values derivable from the analysis (commit SHA,
digests, versions) plus a single recorded UTC timestamp appear here.  No other
wall-clock values, random IDs, or unordered collections.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import DOCKER_IMAGE, PROMPT_VERSIONS, RECEIPT_VERSION, SCHEMA_VERSION, TOOL_VERSION
from .models import Evaluation, InstallResult, Verdict
from .utils import LieDetectorError, canonical_json, sha256_file, sha256_text

RECEIPT_NAME = "verification_receipt.json"
SIDECAR_NAME = "verification_receipt.sha256"

#: Python version inside the pinned execution image.
CONTAINER_PYTHON_VERSION = "3.12"


def environment_fingerprint() -> str:
    """Deterministic fingerprint of the execution environment."""
    return sha256_text(
        canonical_json(
            {
                "docker_image": DOCKER_IMAGE,
                "python_version": CONTAINER_PYTHON_VERSION,
                "tool_version": TOOL_VERSION,
            }
        )
    )[:16]


def build_receipt(
    repo_url: str,
    commit_sha: str,
    timestamp_utc: str,
    readme_sha256: str,
    install: InstallResult | None,
    evaluations: list[Evaluation],
    image: str = DOCKER_IMAGE,
) -> dict[str, Any]:
    """Assemble the receipt dictionary from validated pipeline outputs.

    ``timestamp_utc`` is the single recorded wall-clock value for the run.
    Artifact paths inside the receipt are relative to the receipt file, so the
    bundle is self-contained and verifiable offline.
    """
    tally = {verdict.value: 0 for verdict in Verdict}
    claims: list[dict[str, Any]] = []
    for ev in sorted(evaluations, key=lambda e: (e.claim.source.line, e.claim.id)):
        record = ev.claim.record()
        entry: dict[str, Any] = dict(record)
        entry["claim_sha256"] = sha256_text(canonical_json(record))
        entry["status"] = "adjudicated" if ev.verdict != Verdict.UNTESTABLE else "untestable"
        entry["verdict"] = ev.verdict.value
        entry["failure_category"] = ev.failure_category.value if ev.failure_category else None
        entry["verdict_confidence"] = ev.verdict_confidence.value
        entry["rationale"] = ev.rationale
        entry["harness_path"] = (
            f"harnesses/{ev.claim.id}.py" if ev.harness_code is not None else None
        )
        entry["harness_sha256"] = (
            sha256_text(ev.harness_code) if ev.harness_code is not None else None
        )
        entry["harness_error"] = ev.harness_error
        entry["executions"] = [
            {
                "run_index": run.run_index,
                "exit_code": run.exit_code,
                "timed_out": run.timed_out,
                "control_passed": run.control_passed,
                "claim_passed": run.claim_passed,
                "log_path": f"logs/{ev.claim.id}_run{run.run_index}.txt",
                "log_sha256": sha256_text(run.log_text()),
            }
            for run in ev.runs
        ]
        tally[ev.verdict.value] += 1
        claims.append(entry)

    receipt: dict[str, Any] = {
        "receipt_version": RECEIPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "prompt_versions": dict(PROMPT_VERSIONS),
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "timestamp_utc": timestamp_utc,
        "python_version": CONTAINER_PYTHON_VERSION,
        "docker_image": image,
        "docker_image_digest": image.split("@", 1)[1] if "@" in image else image,
        "environment_fingerprint": environment_fingerprint(),
        "readme_path": "artifacts/README.md",
        "readme_sha256": readme_sha256,
        "install": (
            {
                "ok": install.ok,
                "exit_code": install.exit_code,
                "log_path": "logs/install.txt",
                "log_sha256": sha256_text(install.log),
            }
            if install is not None
            else None
        ),
        "verdict_tally": tally,
        "claims": claims,
    }
    return receipt


def write_receipt(receipt: dict[str, Any], out_dir: Path) -> tuple[Path, str]:
    """Write the canonical receipt and its sidecar hash; return (path, hash)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(receipt)
    receipt_path = out_dir / RECEIPT_NAME
    receipt_path.write_text(payload, encoding="utf-8")
    digest = sha256_text(payload)
    (out_dir / SIDECAR_NAME).write_text(f"{digest}  {RECEIPT_NAME}\n", encoding="utf-8")
    return receipt_path, digest


@dataclass(frozen=True)
class Check:
    """One verification check result."""

    name: str
    ok: bool
    detail: str


def verify_receipt(receipt_path: Path) -> list[Check]:
    """Recompute every hash from stored artifacts and validate the receipt.

    Confirms that: the receipt file is canonical JSON, its hash matches the
    sidecar, and every stored artifact (README, harnesses, execution logs,
    install log) plus every claim record hashes to the value the receipt
    committed to.
    """
    checks: list[Check] = []
    base = receipt_path.parent
    if not receipt_path.is_file():
        raise LieDetectorError(f"receipt not found: {receipt_path}")
    raw = receipt_path.read_text(encoding="utf-8")
    try:
        receipt: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LieDetectorError(f"receipt is not valid JSON: {exc}") from exc

    canonical = canonical_json(receipt)
    checks.append(
        Check("receipt-canonical-form", raw == canonical, "receipt bytes are canonical JSON")
    )

    digest = sha256_text(raw)
    sidecar_path = base / SIDECAR_NAME
    if sidecar_path.is_file():
        sidecar = sidecar_path.read_text(encoding="utf-8").split()[0]
        checks.append(
            Check(
                "sidecar-hash",
                sidecar == digest,
                f"sidecar={sidecar[:16]}... recomputed={digest[:16]}...",
            )
        )
    else:
        checks.append(Check("sidecar-hash", False, "sidecar file missing"))

    def check_file(name: str, rel: str, expected: str) -> None:
        path = base / rel
        if not path.is_file():
            checks.append(Check(name, False, f"missing artifact {rel}"))
            return
        actual = sha256_file(path)
        checks.append(Check(name, actual == expected, rel))

    check_file("readme-hash", str(receipt["readme_path"]), str(receipt["readme_sha256"]))

    install = receipt.get("install")
    if isinstance(install, dict):
        check_file("install-log-hash", str(install["log_path"]), str(install["log_sha256"]))

    record_keys = (
        "id",
        "source",
        "claim_type",
        "hypothesis",
        "interpretation_notes",
        "confidence",
        "suggested_strategy",
    )
    for entry in receipt.get("claims", []):
        cid = entry["id"]
        record = {key: entry[key] for key in record_keys}
        recomputed = sha256_text(canonical_json(record))
        checks.append(
            Check(f"claim-record-hash:{cid}", recomputed == entry["claim_sha256"], cid)
        )
        if entry.get("harness_path"):
            check_file(
                f"harness-hash:{cid}",
                str(entry["harness_path"]),
                str(entry["harness_sha256"]),
            )
        for execution in entry.get("executions", []):
            check_file(
                f"log-hash:{cid}:run{execution['run_index']}",
                str(execution["log_path"]),
                str(execution["log_sha256"]),
            )
    return checks
