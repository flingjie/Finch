"""idea 服务：纯函数集合（判断 + 写样稿 + Critic + 对象构建），不访问 DB。

CLI 负责 IO 与落库；本模块不依赖 finch.cli，避免循环导入。
"""

import hashlib
import json
from pathlib import Path
from typing import cast

from finch.author.models import AuthorPost
from finch.content.checkers.base import CheckResult
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    ContentScope,
    IntendedEffect,
    PositionSource,
    SuccessCriterion,
)
from finch.content.models import Draft, DraftKind
from finch.content.writer import _render_failed_checks, _render_job_context
from finch.evidence.models import EvidenceCard
from finch.idea.models import AssessIdeaOutput, RewriteIdeaOutput, WriteIdeaOutput
from finch.llm.base import StructuredInferenceRunner

_ASSESS_PROMPT_PATH = Path("prompts/assess-idea.md")
_WRITE_PROMPT_PATH = Path("prompts/write-idea.md")
_MAX_ASSESS_CARDS = 50

_IDEA_SUCCESS_CRITERION = SuccessCriterion(
    id="idea_human_review",
    description="人工审核确认是否发布",
    measurement="human",
)

_IDEA_REWRITE_PROMPT = """\
You rewrite a draft to address specific critic check failures. Return JSON matching the schema.
Instructions:
- Keep the same voice and personal-judgment framing as the Original draft.
- Fix exactly the failures listed under Failed checks. Do NOT restyle, polish, or improve
  the rest of the draft — change only what is needed to resolve the listed failures.

{job_context}## Original draft
{body}

## Failed checks
{rewrite_instructions}
"""


def recent_author_posts(posts: list[AuthorPost], limit: int = 25) -> list[AuthorPost]:
    """返回作者最近的原创/回复（按 published_at 降序，取前 limit）。"""
    filtered = [p for p in posts if p.kind in {"original", "reply"}]
    filtered.sort(key=lambda p: p.published_at, reverse=True)
    return filtered[:limit]


def build_content_job(text: str, assessment: AssessIdeaOutput) -> ContentJob:
    """把评估结果转换成已确认立场的 ContentJob（id 由文本哈希确定，幂等）。"""
    job_id = "idea_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return ContentJob(
        id=job_id,
        source_card_ids=list(assessment.matched_evidence_ids),
        candidate_id=None,
        reader_problem=assessment.reader_problem or "",
        audience=assessment.audience or "",
        intended_effect=IntendedEffect(
            understand=assessment.understand or "",
            believe=assessment.believe,
            action=assessment.action,
        ),
        author_position=AuthorPosition(
            claim=assessment.claim or "",
            decision=assessment.decision or "",
            tradeoff=assessment.tradeoff or "",
            change_mind_if=assessment.change_mind_if,
            confirmed=True,
            position_source=PositionSource.HUMAN_CONFIRMED,
        ),
        success_criteria=[_IDEA_SUCCESS_CRITERION],
        recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.READY,
        scope=ContentScope.BOUNDED_LESSON,
    )


def build_draft(job: ContentJob, body: str) -> Draft:
    """把样稿正文包成 idea 草稿（claims 恒为空，run_id 用常量 idea）。"""
    return Draft(
        id=f"draft_{job.id}",
        kind=DraftKind.ORIGINAL,
        candidate_id=None,
        language="zh",
        body=body,
        claims=[],
        content_job_id=job.id,
        position_statement=job.author_position.decision if job.author_position else "",
        run_id="idea",
    )


def _slim_cards(cards: list[EvidenceCard]) -> list[dict]:
    slim: list[dict] = []
    for card in cards[:_MAX_ASSESS_CARDS]:
        slim.append({"id": card.id, "claim": card.claim, "topics": card.topics})
    return slim


def _slim_posts(posts: list[AuthorPost]) -> list[dict]:
    return [
        {
            "kind": p.kind,
            "body": p.body,
            "url": p.url,
            "published_at": p.published_at.isoformat(),
        }
        for p in posts
    ]


def assess_idea(
    runner: StructuredInferenceRunner,
    text: str,
    cards: list[EvidenceCard],
    recent_posts: list[AuthorPost],
) -> AssessIdeaOutput:
    """调用①：判断能否发 + 提取 job 语境。"""
    prompt = _ASSESS_PROMPT_PATH.read_text().format(
        text=text,
        cards=json.dumps(_slim_cards(cards), ensure_ascii=False),
        recent_posts=json.dumps(_slim_posts(recent_posts), ensure_ascii=False),
    )
    return cast(AssessIdeaOutput, runner.run(prompt, AssessIdeaOutput))


def write_idea(
    runner: StructuredInferenceRunner,
    text: str,
    assessment: AssessIdeaOutput,
    cards: list[EvidenceCard],
) -> str:
    """调用②：把用户原文 + 证据扩写成样稿正文。"""
    prompt = _WRITE_PROMPT_PATH.read_text().format(
        text=text,
        core_point=assessment.core_point or "",
        audience=assessment.audience or "",
        claim=assessment.claim or "",
        decision=assessment.decision or "",
        tradeoff=assessment.tradeoff or "",
        cards=json.dumps([c.model_dump(mode="json") for c in cards], ensure_ascii=False),
    )
    out = cast(WriteIdeaOutput, runner.run(prompt, WriteIdeaOutput))
    return out.body


def rewrite_idea(
    runner: StructuredInferenceRunner,
    draft: Draft,
    failed_checks: list[CheckResult],
    job: ContentJob | None,
) -> Draft:
    """按 Critic 失败项定向重写（只回传新正文，claims 恒为空）。"""
    prompt = _IDEA_REWRITE_PROMPT.format(
        body=draft.body,
        job_context=_render_job_context(job) if job is not None else "",
        rewrite_instructions=_render_failed_checks(failed_checks),
    )
    out = cast(RewriteIdeaOutput, runner.run(prompt, RewriteIdeaOutput))
    return draft.model_copy(update={"body": out.body})
