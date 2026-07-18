"""Unit tests for CLI parsing, verify command and doctor exit codes."""

from __future__ import annotations

from pathlib import Path

from liedetector.cli import build_parser, main


def test_version_command(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_help_for_every_command() -> None:
    parser = build_parser()
    argv = {
        "run": ["run", "https://github.com/u/r"],
        "verify": ["verify", "x"],
        "demo": ["demo"],
        "doctor": ["doctor"],
        "version": ["version"],
    }
    for cmd, args in argv.items():
        assert parser.parse_args(args).command == cmd


def test_verify_command_on_good_bundle(tmp_path: Path, toy_repo_dir: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from liedetector.cli import run_pipeline

    from .conftest import (
        PASSING_HARNESS,
        FakeExecutor,
        FakeLLM,
        extraction_response,
        harness_response,
    )

    llm = FakeLLM(
        [
            extraction_response(
                [{"quote": "`toylib.add(2, 3)` returns `5`.",
                  "claim_type": "deterministic", "hypothesis": "add"}]
            ),
            harness_response(PASSING_HARNESS.format(package="toylib", hypothesis="add")),
        ]
    )
    result = run_pipeline(
        str(toy_repo_dir), llm=llm, executor=FakeExecutor(),
        receipts_dir=tmp_path / "receipts", reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses", allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    assert main(["verify", str(result.receipt_path)]) == 0
    assert "verification OK" in capsys.readouterr().out


def test_verify_command_detects_tamper(tmp_path: Path, toy_repo_dir: Path) -> None:
    from liedetector.cli import run_pipeline

    from .conftest import (
        PASSING_HARNESS,
        FakeExecutor,
        FakeLLM,
        extraction_response,
        harness_response,
    )

    llm = FakeLLM(
        [
            extraction_response(
                [{"quote": "`toylib.add(2, 3)` returns `5`.",
                  "claim_type": "deterministic", "hypothesis": "add"}]
            ),
            harness_response(PASSING_HARNESS.format(package="toylib", hypothesis="add")),
        ]
    )
    result = run_pipeline(
        str(toy_repo_dir), llm=llm, executor=FakeExecutor(),
        receipts_dir=tmp_path / "receipts", reports_dir=tmp_path / "reports",
        harnesses_dir=tmp_path / "harnesses", allow_local=True,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    bundle = result.receipt_path.parent
    (bundle / "artifacts" / "README.md").write_text("tampered\n", encoding="utf-8")
    assert main(["verify", str(result.receipt_path)]) == 1


def test_verify_missing_file_returns_error_code(tmp_path: Path) -> None:
    assert main(["verify", str(tmp_path / "nope.json")]) == 2
