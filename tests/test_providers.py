"""Unit tests for the OpenAI-compatible client, provider dispatch, and .env loading.

Hermetic: ``openai.OpenAI`` is monkeypatched with a queued fake, mirroring the
``FakeLLM``/``FakeExecutor`` pattern used elsewhere. No network, no API key.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from liedetector.cli import _build_llm_client, _load_dotenv, build_parser
from liedetector.llm import AnthropicClient, OpenAIClient


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Queued responses; an ``Exception`` entry is raised instead of returned."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeCompletion:
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeCompletion(item)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeOpenAI:
    """Stand-in for ``openai.OpenAI`` capturing constructor kwargs."""

    def __init__(self, responses: list[Any] | None = None, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.chat = _FakeChat(_FakeCompletions(list(responses or [])))


def _patch_openai(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> _FakeOpenAI:
    holder: dict[str, _FakeOpenAI] = {}

    def factory(**kwargs: Any) -> _FakeOpenAI:
        instance = _FakeOpenAI(responses, **kwargs)
        holder["instance"] = instance
        return instance

    monkeypatch.setattr("openai.OpenAI", factory)
    return holder  # type: ignore[return-value]


# --- OpenAIClient.complete() -------------------------------------------------


def test_openai_client_uses_json_schema_when_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = _patch_openai(monkeypatch, ['{"claims": []}'])
    client = OpenAIClient(model="test-model", base_url="http://x", api_key="k")
    result = client.complete("sys", "user", {"type": "object"})
    assert result == '{"claims": []}'
    assert client._supports_json_schema is True
    instance = holder["instance"]  # type: ignore[index]
    assert instance.chat.completions.calls[0]["response_format"]["type"] == "json_schema"


def test_openai_client_falls_back_to_json_object_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_openai(monkeypatch, [RuntimeError("json_schema not supported"), '{"claims": []}'])
    client = OpenAIClient(model="test-model")
    result = client.complete("sys", "user", {"type": "object"})
    assert result == '{"claims": []}'
    assert client._supports_json_schema is False


def test_openai_client_skips_json_schema_probe_after_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai(
        monkeypatch, [RuntimeError("nope"), '{"a": 1}', '{"b": 2}']
    )
    client = OpenAIClient()
    first = client.complete("s", "u", {})
    second = client.complete("s", "u", {})
    assert first == '{"a": 1}'
    assert second == '{"b": 2}'
    instance = holder["instance"]  # type: ignore[index]
    # 3 calls total: failed probe + 2 fallback completions; no repeat probing.
    assert len(instance.chat.completions.calls) == 3
    assert all(
        call["response_format"]["type"] == "json_object"
        for call in instance.chat.completions.calls[1:]
    )


def test_openai_client_reraises_error_once_schema_support_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_openai(monkeypatch, ['{"ok": true}', RuntimeError("transient failure")])
    client = OpenAIClient()
    client.complete("s", "u", {})  # confirms json_schema is supported
    with pytest.raises(RuntimeError, match="transient failure"):
        client.complete("s", "u", {})


def test_openai_client_forwards_base_url_and_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = _patch_openai(monkeypatch, ["{}"])
    OpenAIClient(base_url="https://api.featherless.ai/v1", api_key="secret")
    instance = holder["instance"]  # type: ignore[index]
    assert instance.kwargs["base_url"] == "https://api.featherless.ai/v1"
    assert instance.kwargs["api_key"] == "secret"


# --- _build_llm_client dispatch ----------------------------------------------


def test_build_llm_client_defaults_to_anthropic() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "https://github.com/u/r"])
    client = _build_llm_client(args)
    assert isinstance(client, AnthropicClient)
    assert client.model == "claude-opus-4-8"


def test_build_llm_client_selects_openai_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    parser = build_parser()
    args = parser.parse_args(
        ["run", "https://github.com/u/r", "--provider", "openai",
         "--base-url", "https://api.featherless.ai/v1"]
    )
    client = _build_llm_client(args)
    assert isinstance(client, OpenAIClient)
    assert client.model == "gpt-4o"


def test_build_llm_client_respects_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    parser = build_parser()
    args = parser.parse_args(
        ["run", "https://github.com/u/r", "--provider", "openai", "--model", "custom-model"]
    )
    client = _build_llm_client(args)
    assert isinstance(client, OpenAIClient)
    assert client.model == "custom-model"


def test_build_llm_client_falls_back_to_featherless_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("FEATHERLESS_API_KEY", "feather-key")
    parser = build_parser()
    args = parser.parse_args(["run", "https://github.com/u/r", "--provider", "openai"])
    client = _build_llm_client(args)
    assert isinstance(client, OpenAIClient)


# --- _load_dotenv --------------------------------------------------------------


def test_load_dotenv_sets_unset_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(
        "FOO=bar\n# a comment\n\nBAZ=qux  # inline comment\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    _load_dotenv()
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "qux"


def test_load_dotenv_does_not_override_existing_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("FOO=fromfile\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOO", "fromenv")
    _load_dotenv()
    assert os.environ["FOO"] == "fromenv"


def test_load_dotenv_noop_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _load_dotenv()  # must not raise
