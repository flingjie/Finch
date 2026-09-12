"""WeeklyReflectionService：把一周的关系、表达、讨论转成下一周的重点（LLM 定性复盘）。

确定性指标仍由代码计算（``weekly_analysis`` + ``compute_relationship_metrics``），本服务
只做「解读」：指标与数据是输入，结论由 LLM 判断，但 LLM 输出不含任何 total / 分数。
"""

from typing import cast

from pydantic import BaseModel, Field

from finch.content.voice import VoiceProfile
from finch.conversations.models import ConversationThread
from finch.engagement.metrics import RelationshipMetrics
from finch.learn.models import Feedback
from finch.learn.weekly import WeeklyReport
from finch.llm.base import StructuredInferenceRunner

_REFLECT_PROMPT = """\
You write a weekly reflection centered on meaningful connections and repeat interactions.
Do not produce a list of generic advice.

Answer exactly five questions:
1. Who did the user form a real back-and-forth exchange with this week?
2. Which interactions got only surface feedback?
3. Which conversations formed a new viewpoint or experiment?
4. Which three relationships should be continued next week?
5. Which expressions sounded more and more like the user?

## Relationship metrics (computed in code)
{relationship_metrics}

## Deterministic metrics (computed in code)
{metrics}

## Conversation threads
{threads}

## Concrete messages (excerpts)
{messages}

## Idea position changes
{idea_diffs}

## Published feedback
{feedbacks}

## Voice profile
{voice}

Respond with JSON matching the schema: insight, strongest_expression, meaningful_connection,
surface_only_interactions, conversations_formed_ideas, continue_relationships, next_practice,
stop_doing, voice_update_candidate, new_idea_candidates (list).
"""


class WeeklyReflection(BaseModel):
    """定性周复盘：五个问题的答案 + 辅助信息。"""

    insight: str
    strongest_expression: str        # Q5: 越来越像自己的表达
    meaningful_connection: str       # Q1: 与谁形成真正的来回交流
    surface_only_interactions: str = ""   # Q2: 只有表面反馈的互动
    conversations_formed_ideas: str = ""  # Q3: 形成新观点/实验的对话
    continue_relationships: str = ""      # Q4: 下周继续的关系
    next_practice: str
    stop_doing: str
    voice_update_candidate: str
    new_idea_candidates: list[str] = Field(default_factory=list)


class WeeklyReflectionService:
    """从关系质量指标 + 周报指标 + 相关数据生成定性复盘。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def reflect(
        self,
        report: WeeklyReport,
        *,
        relationship_metrics: RelationshipMetrics | None = None,
        feedbacks: list[Feedback] | None = None,
        threads: list[ConversationThread] | None = None,
        voice_profile: VoiceProfile | None = None,
        message_excerpts: list[str] | None = None,
        idea_diffs: list[str] | None = None,
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
            "do_not_write_rate": report.do_not_write_rate,
            "rewritten_drafts": report.rewritten_drafts,
        }
        rel = relationship_metrics or RelationshipMetrics()
        profile = voice_profile if voice_profile is not None else VoiceProfile()
        return cast(
            WeeklyReflection,
            self.runner.run(
                _REFLECT_PROMPT.format(
                    relationship_metrics=_render_metrics(rel.model_dump()),
                    metrics=_render_metrics(metrics),
                    threads=_render_threads(threads or []),
                    messages=_render_message_excerpts(message_excerpts or []),
                    idea_diffs=_render_idea_diffs(idea_diffs or []),
                    feedbacks=_render_feedbacks(feedbacks or []),
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


def _render_threads(threads: list[ConversationThread]) -> str:
    if not threads:
        return "(none)"
    return "\n".join(
        f"- [{t.id}] {t.topic} (open={len(t.open_questions)}, agreements={len(t.agreements)}, "
        f"experiments={len(t.possible_experiments)}, triggers={len(t.pending_triggers)})"
        for t in threads
    )


def _render_message_excerpts(excerpts: list[str]) -> str:
    if not excerpts:
        return "(none — no concrete messages supplied)"
    return "\n".join(f"- {e}" for e in excerpts[:8])


def _render_idea_diffs(diffs: list[str]) -> str:
    if not diffs:
        return "(none — no idea position changes this week, or honestly unchanged)"
    return "\n".join(f"- {d}" for d in diffs[:8])


def idea_revision_diff_lines(jobs: list) -> list[str]:
    """Build before/after lines from ContentJob.position_revisions for weekly reflection."""
    lines: list[str] = []
    for job in jobs:
        revisions = getattr(job, "position_revisions", None) or []
        if len(revisions) < 2:
            if revisions:
                lines.append(
                    f"[{job.id}] single revision: {revisions[-1].claim} "
                    f"(reason={revisions[-1].change_reason or 'n/a'})"
                )
            else:
                lines.append(f"[{job.id}] unchanged — no position revisions recorded")
            continue
        before, after = revisions[-2], revisions[-1]
        lines.append(
            f"[{job.id}] before={before.claim!r} → after={after.claim!r} "
            f"(reason={after.change_reason or 'n/a'}; sources={after.source_refs})"
        )
    return lines


def render_reflection(reflection: WeeklyReflection) -> str:
    """把 WeeklyReflection 渲染为编辑式摘要：先下周重点，再折叠五问。"""
    practice = reflection.next_practice or "(未给出)"
    lines = [
        f"下周只练：{practice}",
        "",
        f"本周真正有来回：{reflection.meaningful_connection or '(none)'}",
        f"下周继续：{reflection.continue_relationships or '(none)'}",
        "",
        "其余：",
        f"- 表面反馈：{reflection.surface_only_interactions or '(none)'}",
        f"- 形成观点的对话：{reflection.conversations_formed_ideas or '(none)'}",
        f"- 越来越像自己：{reflection.strongest_expression or '(none)'}",
        f"- 停止做：{reflection.stop_doing or '(none)'}",
        f"- 声音更新候选：{reflection.voice_update_candidate or '(none)'}",
    ]
    if reflection.new_idea_candidates:
        lines.append("- 新 Idea 候选：" + "；".join(reflection.new_idea_candidates))
    lines += [
        "",
        "uv run finch practice start --idea <id> --attempt \"...\"",
        "uv run finch connect daily",
        "uv run finch voice propose",
    ]
    return "\n".join(lines)
