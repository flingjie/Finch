"""ArticleAnalysisService：结构化推理生成 ArticleReport。

确定性字段由代码覆盖，不信任模型输出。
"""

import hashlib
from pathlib import Path
from typing import cast

from finch.article.models import ArticleReport
from finch.llm.base import StructuredInferenceRunner
from finch.style.source_resolver import ResolvedSource

_ANALYZER_VERSION = "1.0.0"
_PROMPT_PATH = Path("prompts/analyze-article.md")


def _report_id(content_hash: str) -> str:
    raw = hashlib.sha256(f"{content_hash}:{_ANALYZER_VERSION}".encode()).hexdigest()
    return f"article_{raw[:16]}"


class ArticleAnalysisService:
    """文章表达分析领域服务。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def analyze(self, source: ResolvedSource) -> ArticleReport:
        prompt = _PROMPT_PATH.read_text().format(body=source.body)
        raw = cast(ArticleReport, self.runner.run(prompt, ArticleReport))
        return raw.model_copy(
            update={
                "id": _report_id(source.content_hash),
                "source_type": source.source_type,
                "source_ref": source.source_ref,
                "content_hash": source.content_hash,
            }
        )
