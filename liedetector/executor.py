"""Execution sandbox: containerised install phase plus double execution.

Security model (one execution path, no demo mode):

- image pinned by digest, never by tag; the resolved digest is recorded in
  the receipt,
- non-root user, read-only repository mount, tmpfs writable directory,
- network enabled only during dependency installation, disabled during
  execution (``--network none``),
- 1 CPU, 1 GB RAM, PID limit, all Linux capabilities dropped,
  ``no-new-privileges``; no privileged mode, no Docker socket, no host
  networking, no mounted secrets,
- hard 120 second timeout per execution -> ``INCONCLUSIVE``, never ``FALSE``,
- containers and temporary filesystems are always cleaned up.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Protocol

from . import DOCKER_IMAGE
from .models import ExecutionRun, InstallResult
from .utils import LieDetectorError

log = logging.getLogger("liedetector.executor")

EXECUTION_TIMEOUT_S = 120
# 600s was too short for real-world ML dependency trees: torch's default
# (CUDA-bundled) wheel plus transformers/accelerate can be several GB, and a
# genuine target repo (unitarity-lab) still exceeded 600s after the tmpfs fix
# above. 1800s is a still-bounded ceiling (never unbounded) sized for that
# class of repo; a timeout still resolves to INCONCLUSIVE, never a crash.
INSTALL_TIMEOUT_S = 1800

_RESULT_LINE = re.compile(r"::(test_control|test_claim)\b.*\b(PASSED|FAILED|ERROR)")


class Executor(Protocol):
    """Interface the pipeline uses; tests inject a fake implementation."""

    @property
    def image_digest(self) -> str: ...

    def install(self, repo_path: Path) -> InstallResult:
        """Install the repository package into the sandbox environment."""
        ...

    def run_harness(self, harness_path: Path, run_index: int) -> ExecutionRun:
        """Execute one harness once inside the locked-down sandbox."""
        ...

    def cleanup(self) -> None:
        """Remove any temporary environment state."""
        ...


def parse_pytest_results(stdout: str) -> tuple[bool | None, bool | None]:
    """Parse ``pytest -v`` output into (control_passed, claim_passed).

    ``None`` means the corresponding test never reported a result (e.g. a
    collection error), which adjudication treats conservatively.
    """
    control: bool | None = None
    claim: bool | None = None
    for match in _RESULT_LINE.finditer(stdout):
        outcome = match.group(2) == "PASSED"
        if match.group(1) == "test_control":
            control = outcome
        else:
            claim = outcome
    return control, claim


class DockerExecutor:
    """Real sandbox backed by Docker with the pinned-digest image."""

    def __init__(self, image: str = DOCKER_IMAGE) -> None:
        self.image = image
        self._env_dir: tempfile.TemporaryDirectory[str] | None = None
        self._repo_path: Path | None = None

    @property
    def image_digest(self) -> str:
        return self.image.split("@", 1)[1]

    def _docker(
        self, args: list[str], timeout: int
    ) -> tuple[int, str, str, bool]:
        name = f"liedetector-{uuid.uuid4().hex[:12]}"
        cmd = ["docker", "run", "--name", name, *args]
        log.debug("docker run", extra={"data": {"args": args[:8]}})
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout, proc.stderr, False
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "kill", name], capture_output=True)
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return -1, stdout, stderr, True
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    def _proxy_install_args(self) -> list[str]:
        """Opt-in proxy/CA accommodation for the network-enabled install phase.

        Off by default.  When ``LIEDETECTOR_INSTALL_HOST_NETWORK=1`` is set the
        install container uses host networking so a loopback egress proxy is
        reachable; ``HTTP(S)_PROXY``/``NO_PROXY`` are forwarded, and
        ``LIEDETECTOR_CA_BUNDLE`` (if set) is mounted read-only and pointed at
        with ``PIP_CERT``/``SSL_CERT_FILE``.  The execution phase never uses
        any of this - it always runs with ``--network none``.
        """
        args: list[str] = []
        if os.environ.get("LIEDETECTOR_INSTALL_HOST_NETWORK") == "1":
            args += ["--network", "host"]
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                    "https_proxy", "http_proxy", "no_proxy"):
            value = os.environ.get(var)
            if value:
                args += ["-e", f"{var}={value}"]
        ca = os.environ.get("LIEDETECTOR_CA_BUNDLE")
        if ca and Path(ca).is_file():
            args += [
                "-v", f"{Path(ca).resolve()}:/ca/bundle.crt:ro",
                "-e", "PIP_CERT=/ca/bundle.crt",
                "-e", "SSL_CERT_FILE=/ca/bundle.crt",
            ]
        return args

    def install(self, repo_path: Path) -> InstallResult:
        """Create a venv volume and install the repo + pytest (network on).

        The repository mount stays read-only (immutable input); the package is
        built from a copy inside the container's writable tmpfs so in-tree
        build artifacts (``*.egg-info``) never touch the source tree.
        """
        self._repo_path = repo_path.resolve()
        self._env_dir = tempfile.TemporaryDirectory(
            prefix="liedetector-env-", ignore_cleanup_errors=True
        )
        env_path = Path(self._env_dir.name)
        env_path.chmod(0o777)
        code, stdout, stderr, timed_out = self._docker(
            [
                "--rm",
                *self._proxy_install_args(),
                "--user", "1000:1000",
                "-e", "HOME=/tmp",
                # 512m was too small for real-world ML dependency trees
                # (torch + transformers + accelerate alone exceed it: "No
                # space left on device" on a genuine target repo). 4g covers
                # heavy scientific/ML stacks while staying a bounded limit.
                "--tmpfs", "/tmp:rw,size=4096m",
                "-v", f"{self._repo_path}:/repo:ro",
                "-v", f"{env_path}:/env:rw",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--pids-limit", "256",
                self.image,
                "sh", "-c",
                "cp -r /repo /tmp/src && python -m venv /env/venv && "
                "/env/venv/bin/pip install --no-cache-dir --quiet /tmp/src pytest",
            ],
            timeout=INSTALL_TIMEOUT_S,
        )
        log_text = f"# install exit_code={code} timed_out={timed_out}\n{stdout}\n{stderr}"
        return InstallResult(ok=code == 0 and not timed_out, exit_code=code, log=log_text)

    def run_harness(self, harness_path: Path, run_index: int) -> ExecutionRun:
        """Run one harness with the network disabled and the sandbox locked."""
        if self._env_dir is None or self._repo_path is None:
            raise LieDetectorError("executor.install() must succeed before run_harness()")
        env_path = Path(self._env_dir.name)
        harness_dir = harness_path.resolve().parent
        code, stdout, stderr, timed_out = self._docker(
            [
                "--rm",
                "--network", "none",
                "--user", "1000:1000",
                "-e", "HOME=/tmp",
                "--read-only",
                "--tmpfs", "/tmp:rw,size=256m",
                "--cpus", "1",
                "--memory", "1g",
                "--pids-limit", "128",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "-v", f"{self._repo_path}:/repo:ro",
                "-v", f"{env_path}:/env:ro",
                "-v", f"{harness_dir}:/harness:ro",
                "-w", "/tmp",
                self.image,
                "/env/venv/bin/python", "-m", "pytest", "-v", "-p", "no:cacheprovider",
                f"/harness/{harness_path.name}",
            ],
            timeout=EXECUTION_TIMEOUT_S,
        )
        control, claim = parse_pytest_results(stdout)
        return ExecutionRun(
            run_index=run_index,
            exit_code=code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            control_passed=control,
            claim_passed=claim,
        )

    def cleanup(self) -> None:
        if self._env_dir is not None:
            env_path = Path(self._env_dir.name)
            # `python -m venv` creates POSIX symlinks inside the venv (e.g.
            # venv/lib64 -> venv/lib). On a Windows host bind-mounting this
            # directory into the container, those symlinks materialise as
            # reparse points that Windows' own filesystem APIs cannot open
            # or traverse (WinError 1920), so a host-side rmtree crashes.
            # Let the container remove its own tree with POSIX semantics
            # first; ignore_cleanup_errors=True above is a defense-in-depth
            # fallback if Docker itself is unavailable at this point.
            subprocess.run(
                ["docker", "run", "--rm", "-v", f"{env_path}:/env:rw", self.image,
                 "sh", "-c", "rm -rf /env/venv"],
                capture_output=True,
            )
            self._env_dir.cleanup()
            self._env_dir = None


def docker_available() -> tuple[bool, str]:
    """Check whether a Docker daemon is reachable (used by ``doctor``)."""
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, proc.stderr.strip()
    return True, proc.stdout.strip()
