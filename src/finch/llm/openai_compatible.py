"""OpenAI 兼容的纯结构化推理 runner（无工具、单次 HTTP 调用）。

对齐 Milo（参考实现）的做法：用标准库 HTTP 直接 POST 到 ``{base_url}/chat/completions``，
不引入 SDK 依赖；``temperature=0``；把 Pydantic JSON Schema 放进 system prompt 让模型
知道确切输出形状；容忍 markdown 代码块。
"""

import json
import os
import urllib.error
import urllib.request

from pydantic import BaseModel

from ..codex.structured_output import StructuredOutputError, load_json, parse_checked
from ..settings import LLMNodeSettings, LLMSettings


class OpenAICompatibleRunner:
    """调用任意 OpenAI 兼容 ``/chat/completions`` 端点（OpenAI / DeepSeek / 网关等）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 90.0,
        max_tokens: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    def run(
        self,
        prompt: str,
        output_model: type[BaseModel],
        *,
        timeout: float | None = None,
        max_attempts: int = 2,
    ) -> BaseModel:
        effective_timeout = self.timeout if timeout is None else timeout
        last_error: Exception | None = None
        for _ in range(max_attempts):
            try:
                return self._run_once(prompt, output_model, timeout=effective_timeout)
            except (json.JSONDecodeError, StructuredOutputError) as exc:
                last_error = exc
        raise StructuredOutputError(
            f"LLM failed to produce valid structured output after "
            f"{max_attempts} attempts: {last_error}"
        ) from last_error

    def _run_once(self, prompt: str, output_model: type[BaseModel], *, timeout: float) -> BaseModel:
        schema = json.dumps(output_model.model_json_schema())
        payload: dict = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a structured-output engine. Return only valid JSON "
                        f"matching this JSON Schema:\n{schema}"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        payload_bytes = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(f"LLM request failed ({exc.code}): {detail}") from exc
        except TimeoutError as exc:
            # urlopen 只把 request() 的 OSError 包成 URLError；getresponse()/read()
            # 超时会直接抛 TimeoutError（CPython http.client），必须单独接住。
            raise RuntimeError(f"LLM request timed out after {timeout:g}s") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc.reason}") from exc

        data = json.loads(body)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM response missing choices")
        first = choices[0]
        # 某些 OpenAI 兼容网关非流式也返回 `delta`，兼容两种形状。
        content = (first.get("message") or {}).get("content")
        if content is None:
            content = (first.get("delta") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM response missing text content")
        return parse_checked(load_json(content), output_model)


def create_runner(llm: LLMSettings, node_name: str | None = None) -> OpenAICompatibleRunner | None:
    """从配置构建 OpenAI 兼容 runner；未配置或缺 api_key 时返回 None（调用方回退 CodexRunner）。"""
    if not llm.base_url:
        return None
    api_key = os.environ.get("LLM_API_KEY") or llm.api_key
    if not api_key:
        return None
    node = llm.for_node(node_name) if node_name else LLMNodeSettings(model=llm.model)
    if not node.model:
        return None
    return OpenAICompatibleRunner(
        base_url=llm.base_url,
        api_key=api_key,
        model=node.model,
        timeout=node.timeout_seconds,
        max_tokens=node.max_output_tokens,
    )
