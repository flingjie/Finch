"""LLM 结构化推理 runner 共同接口。"""

from typing import Protocol

from pydantic import BaseModel


class StructuredInferenceRunner(Protocol):
    """一次调用返回一个 Pydantic 模型的推理接口。

    CodexRunner（Agent 执行）与 OpenAICompatibleRunner（纯结构化 HTTP）都满足此接口，
    使 judge 等纯结构化节点可以在这两种后端之间切换。
    """

    def run(
        self,
        prompt: str,
        output_model: type[BaseModel],
        *,
        timeout: float = ...,
    ) -> BaseModel: ...
