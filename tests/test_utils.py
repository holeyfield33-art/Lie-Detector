"""Unit tests for hashing, canonical JSON, URL validation and the workspace."""

from __future__ import annotations

from pathlib import Path

import pytest

from liedetector.utils import (
    LieDetectorError,
    Workspace,
    canonical_json,
    sha256_text,
    validate_github_url,
)


def test_canonical_json_is_byte_stable() -> None:
    a = canonical_json({"b": 1, "a": [1, 2], "c": {"y": None, "x": "é"}})
    b = canonical_json({"c": {"x": "é", "y": None}, "a": [1, 2], "b": 1})
    assert a == b
    assert a == '{"a":[1,2],"b":1,"c":{"x":"é","y":null}}'


def test_sha256_text() -> None:
    assert sha256_text("") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/user/repo",
        "https://github.com/user/repo.git",
        "https://github.com/user-name/re.po/",
    ],
)
def test_valid_github_urls(url: str) -> None:
    validate_github_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/user/repo",
        "https://gitlab.com/user/repo",
        "https://github.com/user",
        "https://github.com/user/repo/tree/main",
        "git@github.com:user/repo.git",
        "file:///tmp/x",
        "; rm -rf /",
    ],
)
def test_invalid_github_urls(url: str) -> None:
    with pytest.raises(LieDetectorError):
        validate_github_url(url)


def test_workspace_clones_and_freezes_commit(toy_repo_dir: Path) -> None:
    with Workspace(str(toy_repo_dir), allow_local=True) as ws:
        assert ws.info is not None
        assert len(ws.info.commit_sha) == 40
        assert ws.info.default_branch == "main"
        assert ws.readme_path().is_file()
        workspace_path = ws.path
    assert not workspace_path.exists()  # cleanup on success


def test_workspace_cleanup_on_failure(toy_repo_dir: Path) -> None:
    captured: list[Path] = []
    with pytest.raises(RuntimeError):
        with Workspace(str(toy_repo_dir), allow_local=True) as ws:
            captured.append(ws.path)
            raise RuntimeError("boom")
    assert not captured[0].exists()


def test_workspace_rejects_non_github_url_by_default() -> None:
    with pytest.raises(LieDetectorError):
        Workspace("/tmp/whatever")
