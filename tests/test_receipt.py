"""Unit tests for receipt construction, hash chain and verify round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from liedetector.models import (
    Claim,
    ClaimType,
    Confidence,
    Evaluation,
    InstallResult,
    Source,
    Verdict,
)
from liedetector.receipt import build_receipt, verify_receipt, write_receipt
from liedetector.utils import LieDetectorError, canonical_json

IMAGE = "python:3.12-slim@sha256:" + "b" * 64


def _evaluation() -> Evaluation:
    claim = Claim(
        id="clm-a",
        source=Source(file="README.md", line=2, quote="the answer is 42"),
        claim_type=ClaimType.DETERMINISTIC,
        hypothesis="returns 42",
        interpretation_notes="n",
        confidence="high",
    )
    return Evaluation(
        claim=claim,
        harness_code="def test_control():\n    pass\n",
        verdict=Verdict.PROVEN,
        verdict_confidence=Confidence.HIGH,
        rationale="both passed",
    )


def _bundle(tmp_path: Path, readme: str, install: InstallResult, ev: Evaluation) -> Path:
    from liedetector.cli import _write_bundle

    bundle = tmp_path / "bundle"
    _write_bundle(bundle, readme, install, [ev])
    return bundle


def test_build_receipt_is_deterministic() -> None:
    ev = _evaluation()
    install = InstallResult(ok=True, exit_code=0, log="ok")
    args = dict(
        repo_url="https://github.com/u/r",
        commit_sha="c" * 40,
        timestamp_utc="2026-01-01T00:00:00Z",
        readme_sha256="d" * 64,
        install=install,
        evaluations=[ev],
        image=IMAGE,
    )
    r1 = build_receipt(**args)  # type: ignore[arg-type]
    r2 = build_receipt(**args)  # type: ignore[arg-type]
    assert canonical_json(r1) == canonical_json(r2)


def test_receipt_omits_report_reference() -> None:
    receipt = build_receipt(
        "https://github.com/u/r", "c" * 40, "2026-01-01T00:00:00Z", "d" * 64,
        InstallResult(True, 0, "ok"), [_evaluation()], IMAGE,
    )
    assert "report" not in canonical_json(receipt).lower()


def test_verify_round_trip_passes(tmp_path: Path) -> None:
    ev = _evaluation()
    install = InstallResult(ok=True, exit_code=0, log="install log")
    readme = "line one\nthe answer is 42\n"
    bundle = _bundle(tmp_path, readme, install, ev)
    from liedetector.utils import sha256_text

    receipt = build_receipt(
        "https://github.com/u/r", "c" * 40, "2026-01-01T00:00:00Z",
        sha256_text(readme), install, [ev], IMAGE,
    )
    path, digest = write_receipt(receipt, bundle)
    checks = verify_receipt(path)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert len(digest) == 64


def test_verify_detects_tampered_readme(tmp_path: Path) -> None:
    ev = _evaluation()
    install = InstallResult(ok=True, exit_code=0, log="install log")
    readme = "line one\nthe answer is 42\n"
    bundle = _bundle(tmp_path, readme, install, ev)
    from liedetector.utils import sha256_text

    receipt = build_receipt(
        "https://github.com/u/r", "c" * 40, "2026-01-01T00:00:00Z",
        sha256_text(readme), install, [ev], IMAGE,
    )
    path, _ = write_receipt(receipt, bundle)
    # Tamper with a stored artifact after the receipt is written.
    (bundle / "artifacts" / "README.md").write_text("tampered!\n", encoding="utf-8")
    checks = verify_receipt(path)
    assert any(c.name == "readme-hash" and not c.ok for c in checks)


def test_verify_detects_tampered_harness(tmp_path: Path) -> None:
    ev = _evaluation()
    install = InstallResult(ok=True, exit_code=0, log="log")
    bundle = _bundle(tmp_path, "the answer is 42\n", install, ev)
    from liedetector.utils import sha256_text

    receipt = build_receipt(
        "https://github.com/u/r", "c" * 40, "2026-01-01T00:00:00Z",
        sha256_text("the answer is 42\n"), install, [ev], IMAGE,
    )
    path, _ = write_receipt(receipt, bundle)
    (bundle / "harnesses" / "clm-a.py").write_text("def test_control(): assert 0\n")
    checks = verify_receipt(path)
    assert any(c.name.startswith("harness-hash") and not c.ok for c in checks)


def test_verify_detects_receipt_edit(tmp_path: Path) -> None:
    ev = _evaluation()
    install = InstallResult(ok=True, exit_code=0, log="log")
    bundle = _bundle(tmp_path, "the answer is 42\n", install, ev)
    from liedetector.utils import sha256_text

    receipt = build_receipt(
        "https://github.com/u/r", "c" * 40, "2026-01-01T00:00:00Z",
        sha256_text("the answer is 42\n"), install, [ev], IMAGE,
    )
    path, _ = write_receipt(receipt, bundle)
    text = path.read_text(encoding="utf-8").replace("PROVEN", "FALSE")
    path.write_text(text, encoding="utf-8")
    checks = verify_receipt(path)
    # Either the sidecar hash or a claim-record hash must catch the edit.
    assert any(not c.ok for c in checks)


def test_verify_rejects_non_json(tmp_path: Path) -> None:
    bad = tmp_path / "verification_receipt.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(LieDetectorError):
        verify_receipt(bad)
