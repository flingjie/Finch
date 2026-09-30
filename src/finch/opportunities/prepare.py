"""贡献制作（规范 §6）：选定机会后按需制作方法卡/回复草稿/演示说明等。

``write_contribution`` 是纯写作步骤：用 ``prompts/prepare-contribution.md`` 让模型按机会的
``proposal``（最小贡献 + 形式 + 预期输出 + 范围）产出正文。正文是可审阅表达方案，不是作者
已确认的立场，材料来源与执行状态由代码侧 ``Artifact`` 的 ``material_origin`` /
``execution_status`` 记录，模型不得声称「已运行」或虚构个人经历。
"""

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from finch.llm.base import StructuredInferenceRunner
from finch.opportunities.models import Opportunity

_PROMPT = Path("prompts/prepare-contribution.md")


class ContributionBodyOutput(BaseModel):
    """贡献正文输出：只回传 body。"""

    body: str


def write_contribution(
    runner: StructuredInferenceRunner, opportunity: Opportunity
) -> str:
    """按机会的 proposal 生成贡献正文（纯正文，不落库、不改状态）。"""
    p = opportunity.proposal
    prompt = _PROMPT.read_text().format(
        topic=opportunity.topic or "(none)",
        entry_kind=opportunity.entry_kind.value if opportunity.entry_kind else "none",
        why_me=opportunity.why_me or "(none)",
        why_continue=opportunity.why_continue or "(none)",
        contribution=p.contribution if p else "(none)",
        form=p.form.value if p else "method_card",
        expected_output=p.expected_output if p else "(none)",
        scope=p.scope if p else "(none)",
    )
    out = cast(ContributionBodyOutput, runner.run(prompt, ContributionBodyOutput))
    return out.body
