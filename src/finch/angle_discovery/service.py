"""AngleDiscoveryService：结构化推理生成 AngleBrief。

确定性字段由代码覆盖，不信任模型输出。
"""

import hashlib
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.angle_discovery.models import AngleBrief
from finch.ingest.resolver import ResolvedSource
from finch.llm.base import StructuredInferenceRunner

_ANGLE_VERSION = "1.1.0"
_PROMPT_PATH = Path("prompts/discover-angles.md")


def brief_id(content_hash: str) -> str:
    raw = hashlib.sha256(f"{content_hash}:{_ANGLE_VERSION}".encode()).hexdigest()
    return f"angle_{raw[:16]}"


class AngleContext(BaseModel):
    """选角上下文（可选输入，缺省为空串/空列表）。"""

    reader: str = ""
    reader_problem: str = ""
    author_context: str = ""
    practice_refs: list[str] = Field(default_factory=list)
    platform: str = ""
    goal: str = ""
    preferred_angles: list[str] = Field(default_factory=list)
    excluded_angles: list[str] = Field(default_factory=list)


def _fmt(items: list[str]) -> str:
    return "\n".join(f"- {it}" for it in items) if items else "（未提供）"


def _txt(value: str) -> str:
    return value or "（未提供）"


class AngleDiscoveryService:
    """文章选角领域服务。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def discover(self, source: ResolvedSource, context: AngleContext | None = None) -> AngleBrief:
        ctx = context or AngleContext()
        prompt = _PROMPT_PATH.read_text().format(
            sample_size=source.sample_size,
            body=source.body,
            reader=_txt(ctx.reader),
            reader_problem=_txt(ctx.reader_problem),
            author_context=_txt(ctx.author_context),
            practice_refs=_fmt(ctx.practice_refs),
            platform=_txt(ctx.platform),
            goal=_txt(ctx.goal),
            preferred_angles=_fmt(ctx.preferred_angles),
            excluded_angles=_fmt(ctx.excluded_angles),
        )
        raw = cast(AngleBrief, self.runner.run(prompt, AngleBrief))
        return raw.model_copy(
            update={
                "id": brief_id(source.content_hash),
                "source_type": source.source_type,
                "source_ref": source.source_ref,
                "content_hash": source.content_hash,
                "coverage": source.coverage,
            }
        )
