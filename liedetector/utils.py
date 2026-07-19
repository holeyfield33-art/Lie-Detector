"""Shared utilities: hashing, canonical JSON, logging and the repository stage.

The repository stage clones a Git source into a temporary workspace, resolves
the default branch, freezes the commit SHA immediately and treats the checkout
as immutable input.  The workspace is always removed on completion.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

log = logging.getLogger("liedetector")

_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(\.git)?/?$"
)


class LieDetectorError(Exception):
    """Base class for user-facing errors raised by the pipeline."""


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex SHA-256 of UTF-8 encoded text."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's contents."""
    return sha256_bytes(path.read_bytes())


def canonical_json(obj: Any) -> str:
    """Serialise to canonical JSON: sorted keys, fixed separators, UTF-8.

    Two semantically equal objects always produce byte-identical output, which
    is what makes the receipt hash chain reproducible.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_github_url(url: str) -> None:
    """Reject anything that is not a plain public GitHub repository URL."""
    if not _GITHUB_URL_RE.match(url):
        raise LieDetectorError(
            f"invalid GitHub repository URL: {url!r} "
            "(expected https://github.com/<owner>/<repo>)"
        )


class _JsonLogFormatter(logging.Formatter):
    """Structured JSON log lines; secrets are never logged by construction."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "stage": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            payload["data"] = extra
        return json.dumps(payload, sort_keys=True)


def configure_logging(verbose: bool) -> None:
    """Send structured JSON logs to stderr; ``--verbose`` enables DEBUG."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonLogFormatter())
    root = logging.getLogger("liedetector")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def _git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LieDetectorError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


@dataclass(frozen=True)
class RepositoryInfo:
    """Frozen identity of the analysed repository."""

    url: str
    commit_sha: str
    default_branch: str


def _clear_readonly(path: str) -> None:
    """Clear the read-only attribute on every entry under ``path``.

    Git marks files under ``.git/objects`` read-only; on Windows, unlike
    POSIX, ``rmtree`` cannot delete a read-only file regardless of directory
    permissions, so a plain ``rmtree`` silently leaks the clone.  This makes
    every entry writable first so the follow-up ``rmtree`` actually succeeds.
    """
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass


class Workspace:
    """Temporary clone of the target repository.

    The commit SHA is recorded immediately after checkout and is the identity
    of the analysis; the working tree is treated as immutable input.  All
    writes go to separate output directories; the workspace is removed on
    exit, success or failure.
    """

    def __init__(self, url: str, allow_local: bool = False) -> None:
        if not allow_local:
            validate_github_url(url)
        self.url = url
        self._tmp: str | None = None
        self.path: Path = Path()
        self.info: RepositoryInfo | None = None

    def __enter__(self) -> Workspace:
        self._tmp = tempfile.mkdtemp(prefix="liedetector-ws-")
        self.path = Path(self._tmp) / "repo"
        log.info("cloning repository", extra={"data": {"url": self.url}})
        _git(["clone", "--quiet", "--depth", "1", self.url, str(self.path)])
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=self.path)
        commit = _git(["rev-parse", "HEAD"], cwd=self.path)
        self.info = RepositoryInfo(url=self.url, commit_sha=commit, default_branch=branch)
        log.info(
            "frozen commit",
            extra={"data": {"commit_sha": commit, "branch": branch}},
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._tmp is not None:
            _clear_readonly(self._tmp)
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    def readme_path(self) -> Path:
        """Path to README.md; raises if the repository has none."""
        path = self.path / "README.md"
        if not path.is_file():
            raise LieDetectorError("repository has no README.md")
        return path
