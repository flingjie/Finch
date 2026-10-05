"""ContentSummaryService：结构化推理生成 ContentSummary。

确定性字段由代码覆盖，不信任模型输出。
"""

import hashlib
from pathlib import Path
from typing import cast

from finch.content_summary.models import ContentSummary
from finch.ingest.resolver import ResolvedSource
from finch.llm.base import StructuredInferenceRunner

_SUMMARY_VERSION = "1.1.0"
_PROMPT_PATH = Path("prompts/summarize-content.md")


def _summary_id(content_hash: str) -> str:
    raw = hashlib.sha256(f"{content_hash}:{_SUMMARY_VERSION}".encode()).hexdigest()
    return f"summary_{raw[:16]}"


class ContentSummaryService:
    """帖子内容摘要领域服务。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def summarize(self, source: ResolvedSource) -> ContentSummary:
        prompt = _PROMPT_PATH.read_text().format(
            sample_size=source.sample_size, body=source.body
        )
        raw = cast(ContentSummary, self.runner.run(prompt, ContentSummary))
        return raw.model_copy(
            update={
                "id": _summary_id(source.content_hash),
                "source_type": source.source_type,
                "source_ref": source.source_ref,
                "content_hash": source.content_hash,
            }
        )
