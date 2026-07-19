"""Command line interface and pipeline orchestration.

Commands::

    liedetector run <repo_url>   # full pipeline
    liedetector verify <receipt> # recompute hashes, validate receipt integrity
    liedetector demo             # run against the bundled toy repo
    liedetector doctor           # check Docker, Python, dependencies
    liedetector version
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from . import DEFAULT_OPENAI_MODEL, DOCKER_IMAGE, TOOL_VERSION
from .adjudicate import adjudicate, adjudicate_harness_failure, adjudicate_install_failure
from .classify import classify
from .executor import DockerExecutor, Executor, docker_available
from .extract import extract_claims
from .llm import AnthropicClient, LLMClient, LLMError, OpenAIClient
from .models import Evaluation, InstallResult
from .receipt import build_receipt, verify_receipt, write_receipt
from .refine import refine
from .report import render_report
from .synthesize import synthesize_harness
from .utils import LieDetectorError, Workspace, configure_logging, sha256_text

log = logging.getLogger("liedetector.cli")


@dataclass(frozen=True)
class PipelineResult:
    """Paths and summary data produced by one pipeline run."""

    receipt_path: Path
    receipt_hash: str
    report_path: Path
    verdict_tally: dict[str, int]
    duration_seconds: float


def _package_name(repo_path: Path) -> str:
    """Best-effort installable/import package name for the repository."""
    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            name = data.get("project", {}).get("name")
            if isinstance(name, str) and name:
                return name.replace("-", "_")
        except tomllib.TOMLDecodeError:
            pass
    return repo_path.name.replace("-", "_")


def _utc_now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_pipeline(
    source_url: str,
    llm: LLMClient,
    executor: Executor,
    receipts_dir: Path,
    reports_dir: Path,
    harnesses_dir: Path,
    allow_local: bool = False,
    timestamp_utc: str | None = None,
) -> PipelineResult:
    """Run the full pipeline and write the receipt bundle plus Truth Report."""
    started = monotonic()
    timestamp = timestamp_utc or _utc_now()

    with Workspace(source_url, allow_local=allow_local) as workspace:
        assert workspace.info is not None
        info = workspace.info
        readme_text = workspace.readme_path().read_text(encoding="utf-8")
        package = _package_name(workspace.path)

        claims = extract_claims(llm, readme_text)
        executable, evaluations = classify(claims)
        executable, refine_failed = refine(executable)
        evaluations.extend(refine_failed)

        harnesses: dict[str, str] = {}
        for claim in executable:
            try:
                harnesses[claim.id] = synthesize_harness(llm, claim, package)
            except LLMError as exc:
                evaluations.append(adjudicate_harness_failure(claim, str(exc)))

        pending = [claim for claim in executable if claim.id in harnesses]
        install: InstallResult | None = None
        try:
            if pending:
                install = executor.install(workspace.path)
                if not install.ok:
                    log.warning("dependency installation failed")
                    for claim in pending:
                        evaluations.append(adjudicate_install_failure(claim))
                else:
                    with tempfile.TemporaryDirectory(prefix="liedetector-h-") as tmp:
                        # The sandbox runs as a non-root user; the staging dir
                        # and harness files must be world-readable to mount.
                        os.chmod(tmp, 0o755)
                        for claim in pending:
                            harness_path = Path(tmp) / f"{claim.id}.py"
                            harness_path.write_text(harnesses[claim.id], encoding="utf-8")
                            harness_path.chmod(0o644)
                            runs = [
                                executor.run_harness(harness_path, run_index)
                                for run_index in (1, 2)
                            ]
                            evaluations.append(
                                adjudicate(claim, harnesses[claim.id], runs, package)
                            )
        finally:
            executor.cleanup()

        # Attach harness code to install-failure evaluations for the record.
        for evaluation in evaluations:
            if evaluation.harness_code is None and evaluation.claim.id in harnesses:
                evaluation.harness_code = harnesses[evaluation.claim.id]

        bundle_dir = receipts_dir / info.commit_sha[:12]
        _write_bundle(bundle_dir, readme_text, install, evaluations)

        receipt = build_receipt(
            repo_url=info.url,
            commit_sha=info.commit_sha,
            timestamp_utc=timestamp,
            readme_sha256=sha256_text(readme_text),
            install=install,
            evaluations=evaluations,
            image=executor.image_digest
            if "@" in executor.image_digest
            else DOCKER_IMAGE.split("@")[0] + "@" + executor.image_digest,
        )
        receipt_path, receipt_hash = write_receipt(receipt, bundle_dir)

        duration = monotonic() - started
        (bundle_dir / "logs" / "run_meta.json").write_text(
            json.dumps({"duration_seconds": round(duration, 3)}), encoding="utf-8"
        )

        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"{info.commit_sha[:12]}.html"
        report_path.write_text(
            render_report(receipt, receipt_hash, bundle_dir, duration),
            encoding="utf-8",
        )

        run_harnesses_dir = harnesses_dir / info.commit_sha[:12]
        if (bundle_dir / "harnesses").is_dir():
            shutil.copytree(
                bundle_dir / "harnesses", run_harnesses_dir, dirs_exist_ok=True
            )

        tally = {str(k): int(v) for k, v in receipt["verdict_tally"].items()}
        return PipelineResult(
            receipt_path=receipt_path,
            receipt_hash=receipt_hash,
            report_path=report_path,
            verdict_tally=tally,
            duration_seconds=duration,
        )


def _write_hashed_text(path: Path, text: str) -> None:
    """Write text as raw UTF-8 bytes, with no newline translation.

    Every artifact written here was hashed in-memory (LF-only, since
    ``Path.read_text`` and f-string log builders never introduce ``\\r\\n``).
    ``Path.write_text`` applies platform newline translation on write, which
    on Windows turns ``\\n`` into ``\\r\\n`` and makes the on-disk bytes no
    longer match the hash the receipt committed to. Writing raw bytes keeps
    the artifact byte-identical to what was hashed on every platform.
    """
    path.write_bytes(text.encode("utf-8"))


def _write_bundle(
    bundle_dir: Path,
    readme_text: str,
    install: InstallResult | None,
    evaluations: list[Evaluation],
) -> None:
    """Write every hashed artifact into the self-contained receipt bundle."""
    (bundle_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "logs").mkdir(parents=True, exist_ok=True)
    _write_hashed_text(bundle_dir / "artifacts" / "README.md", readme_text)
    if install is not None:
        _write_hashed_text(bundle_dir / "logs" / "install.txt", install.log)
    for evaluation in evaluations:
        if evaluation.harness_code is not None:
            (bundle_dir / "harnesses").mkdir(parents=True, exist_ok=True)
            _write_hashed_text(
                bundle_dir / "harnesses" / f"{evaluation.claim.id}.py", evaluation.harness_code
            )
        for run in evaluation.runs:
            _write_hashed_text(
                bundle_dir / "logs" / f"{evaluation.claim.id}_run{run.run_index}.txt",
                run.log_text(),
            )


def _print_summary(result: PipelineResult) -> None:
    print("Verdict tally:")
    for verdict in ("PROVEN", "FALSE", "INCONCLUSIVE", "UNTESTABLE"):
        print(f"  {verdict:<13} {result.verdict_tally.get(verdict, 0)}")
    print(f"Receipt:      {result.receipt_path}")
    print(f"Receipt hash: {result.receipt_hash}")
    print(f"Truth Report: {result.report_path}")


def _build_llm_client(args: argparse.Namespace) -> LLMClient:
    """Instantiate the correct LLM client based on CLI arguments."""
    if args.provider == "openai":
        base_url = args.base_url or os.environ.get("OPENAI_BASE_URL")
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("FEATHERLESS_API_KEY")
        )
        model = args.model or DEFAULT_OPENAI_MODEL
        return OpenAIClient(model=model, base_url=base_url, api_key=api_key)
    else:
        model = args.model or "claude-opus-4-8"
        return AnthropicClient(model=model)


def _cmd_run(args: argparse.Namespace) -> int:
    llm: LLMClient = _build_llm_client(args)
    executor: Executor = DockerExecutor()
    result = run_pipeline(
        args.repo_url,
        llm=llm,
        executor=executor,
        receipts_dir=Path(args.receipts_dir),
        reports_dir=Path(args.reports_dir),
        harnesses_dir=Path(args.harnesses_dir),
    )
    _print_summary(result)
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    toy_repo = Path(__file__).resolve().parent.parent / "demo" / "toy_repo"
    if not toy_repo.is_dir():
        raise LieDetectorError(
            "bundled toy repo not found; run `liedetector demo` from a source checkout"
        )
    with tempfile.TemporaryDirectory(prefix="liedetector-demo-") as tmp:
        demo_repo = Path(tmp) / "toy_repo"
        shutil.copytree(toy_repo, demo_repo)
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "Lie Detector Demo",
                "GIT_AUTHOR_EMAIL": "demo@liedetector.invalid",
                "GIT_COMMITTER_NAME": "Lie Detector Demo",
                "GIT_COMMITTER_EMAIL": "demo@liedetector.invalid",
                "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
                "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
            }
        )
        for cmd in (
            ["git", "init", "--quiet", "--initial-branch=main"],
            ["git", "add", "-A"],
            ["git", "commit", "--quiet", "-m", "toy repo"],
        ):
            subprocess.run(cmd, cwd=demo_repo, env=env, check=True, capture_output=True)
        llm: LLMClient = _build_llm_client(args)
        executor: Executor = DockerExecutor()
        result = run_pipeline(
            str(demo_repo),
            llm=llm,
            executor=executor,
            receipts_dir=Path(args.receipts_dir),
            reports_dir=Path(args.reports_dir),
            harnesses_dir=Path(args.harnesses_dir),
            allow_local=True,
        )
    _print_summary(result)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    checks = verify_receipt(Path(args.receipt))
    failed = [check for check in checks if not check.ok]
    for check in checks:
        marker = "ok " if check.ok else "FAIL"
        print(f"[{marker}] {check.name}: {check.detail}")
    if failed:
        print(f"\nverification FAILED ({len(failed)}/{len(checks)} checks failed)")
        return 1
    print(f"\nverification OK ({len(checks)} checks passed)")
    return 0


def _cmd_doctor(_: argparse.Namespace) -> int:
    ok = True
    print(f"liedetector {TOOL_VERSION}")
    print(f"python: {sys.version.split()[0]}")

    if shutil.which("git"):
        print("[ok ] git available")
    else:
        ok = False
        print("[FAIL] git not found on PATH")

    docker_ok, detail = docker_available()
    if docker_ok:
        print(f"[ok ] docker daemon reachable (server {detail})")
    else:
        ok = False
        print(f"[FAIL] docker unavailable: {detail}")
    print(f"      sandbox image (pinned by digest): {DOCKER_IMAGE}")

    # Anthropic provider check
    try:
        import anthropic  # noqa: F401

        print("[ok ] anthropic SDK installed")
    except ImportError:
        print("[warn] anthropic SDK not installed (pip install anthropic)")
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print("[ok ] Anthropic credential present in environment")
    else:
        print("[warn] no ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN set "
              "(the SDK may still resolve a stored profile)")

    # OpenAI-compatible provider check
    try:
        import openai  # noqa: F401

        print("[ok ] openai SDK installed")
    except ImportError:
        print("[warn] openai SDK not installed (pip install openai)")
    openai_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("FEATHERLESS_API_KEY")
    if openai_key:
        print("[ok ] OpenAI-compatible credential present in environment")
    else:
        print("[warn] no OPENAI_API_KEY or FEATHERLESS_API_KEY set")
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        print(f"[info] OPENAI_BASE_URL set to {base_url}")

    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``liedetector`` CLI."""
    parser = argparse.ArgumentParser(
        prog="liedetector",
        description="Turn READMEs into tests: verify factual claims with executable harnesses.",
    )
    parser.add_argument("--verbose", action="store_true", help="enable DEBUG logging")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_output_dirs(p: argparse.ArgumentParser) -> None:
        p.add_argument("--receipts-dir", default="receipts", help="receipt bundle directory")
        p.add_argument("--reports-dir", default="reports", help="Truth Report directory")
        p.add_argument("--harnesses-dir", default="harnesses", help="harness copy directory")

    def add_provider_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--provider",
            choices=["anthropic", "openai"],
            default="anthropic",
            help="LLM provider backend (default: anthropic)",
        )
        p.add_argument(
            "--model",
            default=None,
            help="override the default model for the chosen provider",
        )
        p.add_argument(
            "--base-url",
            default=None,
            help="API base URL for OpenAI-compatible providers "
            "(e.g. https://api.featherless.ai/v1). Also settable via OPENAI_BASE_URL env var.",
        )

    p_run = sub.add_parser("run", help="run the full pipeline against a GitHub repository")
    p_run.add_argument("repo_url", help="https://github.com/<owner>/<repo>")
    add_output_dirs(p_run)
    add_provider_args(p_run)
    p_run.set_defaults(func=_cmd_run)

    p_verify = sub.add_parser(
        "verify", help="recompute hashes and validate a verification receipt"
    )
    p_verify.add_argument("receipt", help="path to verification_receipt.json")
    p_verify.set_defaults(func=_cmd_verify)

    p_demo = sub.add_parser("demo", help="run the pipeline against the bundled toy repo")
    add_output_dirs(p_demo)
    add_provider_args(p_demo)
    p_demo.set_defaults(func=_cmd_demo)

    p_doctor = sub.add_parser("doctor", help="check Docker, Python and dependencies")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_version = sub.add_parser("version", help="print the tool version")
    p_version.set_defaults(func=lambda _: print(TOOL_VERSION) or 0)

    return parser


def _load_dotenv() -> None:
    """Load a ``.env`` file from the current working directory into ``os.environ``.

    Only sets variables that are not already present in the environment.
    Supports simple ``KEY=value`` lines, inline comments (``#``), and blank
    lines.  Does NOT support quoting, multi-line values, or variable expansion.
    """
    dotenv_path = Path.cwd() / ".env"
    if not dotenv_path.is_file():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip inline comments from value (simple # after whitespace).
        if "#" in value:
            for i, ch in enumerate(value):
                if ch == "#" and (i == 0 or value[i - 1].isspace()):
                    value = value[:i].rstrip()
                    break
        if key and key not in os.environ:
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    _load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    try:
        result = args.func(args)
        return int(result or 0)
    except LieDetectorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
