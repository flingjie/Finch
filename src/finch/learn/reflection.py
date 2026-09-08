"""WeeklyReflectionService：把一周的表达/修改/讨论/结果转成下一周一个训练重点（LLM 定性复盘）。

确定性指标仍由 ``weekly_analysis``（learn/weekly.py）计算，本服务只做「解读」：
指标与数据是输入，结论由 LLM 判断，但 LLM 输出不含任何 total / 分数。
"""

from typing import cast

from pydantic import BaseModel, Field

from finch.content.voice import VoiceProfile
from finch.engagement.models import ConversationEvidence
from finch.learn.models import Feedback
from finch.learn.weekly import WeeklyReport
from finch.llm.base import StructuredInferenceRunner

_REFLECT_PROMPT = """\
You write a weekly reflection that turns the past week's expression, revisions, discussions,
and results into ONE training focus for next week. Do not produce a list of generic advice.

Answer only four questions:
1. What did the user actually figure out this week?
2. Which expression sounded most like themselves?
3. Which exchange produced a new connection or a new question?
4. What ONE expression problem should they train next week?

## Deterministic metrics (computed in code)
{metrics}

## Published feedback
{feedbacks}

## Conversation evidence
{conversations}

## Voice profile
{voice}

Respond with JSON matching the schema: insight, strongest_expression,
meaningful_connection, next_practice, stop_doing, voice_update_candidate,
new_idea_candidates (list).
"""


class WeeklyReflection(BaseModel):
    """定性周复盘：四个问题的答案 + 一个训练重点。"""

    insight: str
    strongest_expression: str
    meaningful_connection: str
    next_practice: str
    stop_doing: str
    voice_update_candidate: str
    new_idea_candidates: list[str] = Field(default_factory=list)


class WeeklyReflectionService:
    """从 WeeklyReport（指标）+ 相关数据生成定性复盘。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def reflect(
        self,
        report: WeeklyReport,
        *,
        feedbacks: list[Feedback] | None = None,
        conversation_evidence: list[ConversationEvidence] | None = None,
        voice_profile: VoiceProfile | None = None,
    ) -> WeeklyReflection:
        metrics = {
            "reviewed_drafts": report.reviewed_drafts,
            "approved": report.approved,
            "skipped": report.skipped,
            "approval_rate": report.approval_rate,
            "evidence_coverage": report.evidence_coverage,
            "decision_density": report.decision_density,
            "generic_sentence_rate": report.generic_sentence_rate,
            "human_correction_rate": report.human_correction_rate,
            "job_completion_rate": report.job_completion_rate,
            "useful_reply_rate": report.useful_reply_rate,
        }
        profile = voice_profile if voice_profile is not None else VoiceProfile()
        return cast(
            WeeklyReflection,
            self.runner.run(
                _REFLECT_PROMPT.format(
                    metrics=_render_metrics(metrics),
                    feedbacks=_render_feedbacks(feedbacks or []),
                    conversations=_render_conversations(conversation_evidence or []),
                    voice=profile.model_dump_json(),
                ),
                WeeklyReflection,
            ),
        )


def _render_metrics(metrics: dict) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in metrics.items())


def _render_feedbacks(feedbacks: list[Feedback]) -> str:
    if not feedbacks:
        return "(none)"
    return "\n".join(
        f"- {fb.draft_id}: learning={fb.learning or '(none)'}"
        for fb in feedbacks
    )


def _render_conversations(conversations: list[ConversationEvidence]) -> str:
    if not conversations:
        return "(none)"
    return "\n".join(
        f"- [{c.kind}] {c.statement}" for c in conversations
    )


def render_reflection(reflection: WeeklyReflection) -> str:
    """把 WeeklyReflection 渲染为 Markdown。"""
    lines = [
        "# Finch Weekly Reflection",
        "",
        "## 本周想清楚了什么",
        f"- {reflection.insight or '(none)'}",
        "",
        "## 最像自己的表达",
        f"- {reflection.strongest_expression or '(none)'}",
        "",
        "## 有意义的交流",
        f"- {reflection.meaningful_connection or '(none)'}",
        "",
        "## 下周训练重点",
        f"- {reflection.next_practice or '(none)'}",
        "",
        "## 停止做",
        f"- {reflection.stop_doing or '(none)'}",
        "",
        "## 声音画像更新候选",
        f"- {reflection.voice_update_candidate or '(none)'}",
    ]
    if reflection.new_idea_candidates:
        lines += ["", "## 新 Idea 候选"]
        lines += [f"- {cand}" for cand in reflection.new_idea_candidates]
    return "\n".join(lines)
