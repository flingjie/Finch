"""idea 服务：纯函数集合（判断 + 写样稿 + Critic + 对象构建），不访问 DB。

CLI 负责 IO 与落库；本模块不依赖 finch.cli，避免循环导入。
"""

import hashlib

from finch.author.models import AuthorPost
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
from finch.idea.models import AssessIdeaOutput

_IDEA_SUCCESS_CRITERION = SuccessCriterion(
    id="idea_human_review",
    description="人工审核确认是否发布",
    measurement="human",
)


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
