"""OpenAI 兼容 runner 测试（mock HTTP，无真实网络）。"""

import json

import pytest
from pydantic import BaseModel

from finch.llm.openai_compatible import OpenAICompatibleRunner


class _Out(BaseModel):
    value: int


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._payload


def _response(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


def _runner():
    return OpenAICompatibleRunner("https://gateway.example/v1", "secret", "deepseek-v4-pro")


def test_runner_posts_and_parses_json(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(_response('{"value": 42}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    out = _runner().run("score this", _Out, timeout=30.0)

    assert out.value == 42
    data = json.loads(captured["request"].data)
    assert data["model"] == "deepseek-v4-pro"
    assert data["temperature"] == 0
    messages = data["messages"]
    assert messages[0]["role"] == "system"
    assert "JSON Schema" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "score this"}
    assert captured["request"].headers["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 30.0
    assert captured["request"].full_url == "https://gateway.example/v1/chat/completions"


def test_runner_tolerates_markdown_fence(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(_response('```json\n{"value": 7}\n```')),
    )
    assert _runner().run("p", _Out).value == 7


def test_runner_retries_on_invalid_json(monkeypatch):
    attempts = {"n": 0}

    def fake_urlopen(request, timeout):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _FakeResponse(_response("not json"))
        return _FakeResponse(_response('{"value": 9}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert _runner().run("p", _Out).value == 9
    assert attempts["n"] == 2


def _runner_with_cap():
    return OpenAICompatibleRunner(
        "https://gateway.example/v1", "secret", "deepseek-v4-flash",
        timeout=180.0, max_tokens=6000,
    )


def test_runner_sends_max_tokens_and_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(_response('{"value": 42}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    _runner_with_cap().run("score this", _Out)

    data = json.loads(captured["request"].data)
    assert data["model"] == "deepseek-v4-flash"
    assert data["max_tokens"] == 6000
    assert captured["timeout"] == 180.0


def test_runner_no_max_tokens_when_unset(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return _FakeResponse(_response('{"value": 1}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    _runner().run("p", _Out)

    assert "max_tokens" not in json.loads(captured["request"].data)


def test_runner_wraps_timeout_error(monkeypatch):
    """urlopen/getresponse 的 TimeoutError 不走 URLError，必须转成 RuntimeError。"""

    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="timed out after 12"):
        _runner().run("p", _Out, timeout=12.0)


def test_runner_wraps_timeout_during_body_read(monkeypatch):
    class _TimeoutBody:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            raise TimeoutError("timed out")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _TimeoutBody(),
    )
    with pytest.raises(RuntimeError, match="timed out after 30"):
        _runner().run("p", _Out, timeout=30.0)
