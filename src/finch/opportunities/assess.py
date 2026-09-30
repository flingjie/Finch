"""机会判断（规范 §5.2/§5.5）：LLM 产出首选机会的 why_me/why_continue/proposal 等字段。

``assess_opportunity`` 用 ``prompts/opportunity.md`` 让模型对「一个人 + 其近期成果」做
机会判断，输出 ``OpportunityDraft``；``build_opportunity`` 确定性把 draft 转为新的
``Opportunity`` 聚合（不推荐 / 无最小贡献 → None）。推荐门禁在代码，模型只产语义判断，
不产总分。
"""

from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.llm.base import StructuredInferenceRunner
from finch.opportunities.models import ContributionForm, EntryKind, Opportunity, Proposal

_PROMPT = Path("prompts/opportunity.md")


class OpportunityDraft(BaseModel):
    """LLM 机会判断输出：可审阅语义字段，无任何数字总分。"""

    topic: str = ""
    entry_kind: EntryKind | None = None
    why_me: str = ""
    why_continue: str = ""
    contribution: str = ""
    form: ContributionForm = ContributionForm.METHOD_CARD
    expected_output: str = ""
    scope: str = ""
    open_questions: list[str] = Field(default_factory=list)
    recommend: bool = False
    skip_reason: str = ""


def assess_opportunity(
    runner: StructuredInferenceRunner,
    *,
    peer_id: str,
    display_name: str,
    platform: str,
    current_work: str,
    why_relevant: str,
    their_artifacts_json: str,
    user_context: str = "",
) -> OpportunityDraft:
    """LLM 判断一条首选机会；失败（超时 / 格式不合法）→ 不推荐 draft（fail-soft）。"""
    prompt = _PROMPT.read_text().format(
        peer_id=peer_id,
        display_name=display_name,
        platform=platform,
        current_work=current_work,
        why_relevant=why_relevant,
        their_artifacts=their_artifacts_json,
        user_context=user_context or "(none)",
    )
    try:
        return cast(OpportunityDraft, runner.run(prompt, OpportunityDraft))
    except Exception:
        return OpportunityDraft(recommend=False, skip_reason="机会判断失败（LLM 异常）")


def build_opportunity(
    draft: OpportunityDraft,
    *,
    opportunity_id: str,
    person_ref: str | None = None,
    thread_ref: str | None = None,
) -> Opportunity | None:
    """确定性把 draft 转为 Opportunity；不推荐或最小贡献为空 → None。"""
    if not draft.recommend or not draft.contribution.strip():
        return None
    return Opportunity(
        id=opportunity_id,
        person_ref=person_ref,
        thread_ref=thread_ref,
        topic=draft.topic,
        entry_kind=draft.entry_kind,
        why_me=draft.why_me,
        why_continue=draft.why_continue,
        proposal=Proposal(
            contribution=draft.contribution.strip(),
            form=draft.form,
            expected_output=draft.expected_output,
            scope=draft.scope,
        ),
        open_questions=list(draft.open_questions),
    )
