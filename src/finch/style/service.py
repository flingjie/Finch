"""WritingStyleService：结构化推理生成 StyleReport；可选 compare 到 VoiceProfile。

确定性字段（id/source_type/source_ref/content_hash/sample_size）由代码覆盖，
不信任模型输出；判断字段由模型产出。compare 是第二次 LLM 调用，只读 VoiceProfile，
不写画像。
"""

import hashlib
from pathlib import Path
from typing import cast

from finch.content.voice import VoiceProfile
from finch.llm.base import StructuredInferenceRunner
from finch.style.models import StyleComparison, StyleReport
from finch.style.source_resolver import ResolvedSource

_ANALYZER_VERSION = "1.0.0"
_PROMPT_PATH = Path("prompts/analyze-writing-style.md")

_COMPARE_PROMPT = """\
Compare a style report against the author's own voice profile. Return three buckets:
- already_shared: ways the author already writes like this (no change needed)
- worth_experimenting: ONE technique worth trying (method, not a sentence to copy)
- not_a_fit: this source's habits that do NOT fit the author's voice

Do not recommend copying signature sentences. Do not auto-update the voice profile.

## Style report
{report}

## Voice profile
{voice}
"""


def _report_id(content_hash: str) -> str:
    raw = hashlib.sha256(f"{content_hash}:{_ANALYZER_VERSION}".encode()).hexdigest()
    return f"style_{raw[:16]}"


class WritingStyleService:
    """写作风格分析领域服务。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def analyze(self, source: ResolvedSource) -> StyleReport:
        prompt = _PROMPT_PATH.read_text().format(
            sample_size=source.sample_size, body=source.body
        )
        raw = cast(StyleReport, self.runner.run(prompt, StyleReport))
        # 确定性字段由代码覆盖，不信模型。
        return raw.model_copy(
            update={
                "id": _report_id(source.content_hash),
                "source_type": source.source_type,
                "source_ref": source.source_ref,
                "content_hash": source.content_hash,
                "sample_size": source.sample_size,
            }
        )

    def compare(self, report: StyleReport, voice: VoiceProfile) -> StyleComparison:
        prompt = _COMPARE_PROMPT.format(
            report=report.model_dump_json(), voice=voice.model_dump_json()
        )
        return cast(StyleComparison, self.runner.run(prompt, StyleComparison))
