"""Unit tests for the idea service."""

from datetime import UTC, datetime, timedelta

from finch.author.models import AuthorPost
from finch.content.jobs import ContentJobStatus, ContentScope, PositionSource
from finch.content.models import DraftKind
from finch.idea.models import AssessIdeaOutput, IdeaAssessment
from finch.idea.service import build_content_job, build_draft, recent_author_posts


def _assessment(**overrides) -> AssessIdeaOutput:
    data = dict(
        status="ready",
        core_point="Graph 的价值是恢复与重放",
        matched_evidence_ids=["ev_1"],
        reader_problem="很多工程师只把 Graph 理解成流程可视化",
        audience="正在构建生产 Agent 的工程师",
        understand="Graph 的核心价值是恢复与重放",
        believe="用可恢复性评价 Graph",
        action="重新审视 Graph 的选型",
        claim="Graph 的主要价值是支持恢复与重放",
        decision="用可恢复性而不是图的复杂度评价 Graph",
        tradeoff="需要持久化状态并处理副作用幂等",
        change_mind_if=None,
    )
    data.update(overrides)
    return AssessIdeaOutput(**data)


def test_build_content_job_confirms_human_position():
    job = build_content_job("我觉得 Agent Graph 的价值是恢复", _assessment())
    assert job.id.startswith("idea_")
    assert job.source_card_ids == ["ev_1"]
    assert job.candidate_id is None
    assert job.recommended_format == DraftKind.ORIGINAL
    assert job.status == ContentJobStatus.READY
    assert job.scope == ContentScope.BOUNDED_LESSON
    assert job.author_position is not None
    assert job.author_position.confirmed is True
    assert job.author_position.position_source == PositionSource.HUMAN_CONFIRMED
    assert job.author_position.decision == "用可恢复性而不是图的复杂度评价 Graph"
    assert job.intended_effect.understand == "Graph 的核心价值是恢复与重放"
    assert job.success_criteria[0].id == "idea_human_review"
    assert job.success_criteria[0].measurement == "human"


def test_build_content_job_deterministic_id():
    a = build_content_job("同一个想法", _assessment())
    b = build_content_job("同一个想法", _assessment())
    assert a.id == b.id


def test_build_draft_idea_fields():
    job = build_content_job("想法", _assessment())
    draft = build_draft(job, "样稿正文")
    assert draft.id == f"draft_{job.id}"
    assert draft.kind == DraftKind.ORIGINAL
    assert draft.language == "zh"
    assert draft.claims == []
    assert draft.content_job_id == job.id
    assert draft.run_id == "idea"
    assert draft.position_statement == "用可恢复性而不是图的复杂度评价 Graph"


def _post(remote_id, published_at, kind="original"):
    return AuthorPost(
        platform="x",
        remote_post_id=remote_id,
        author_account_id="acct",
        kind=kind,
        body=f"post {remote_id}",
        url=f"https://x.com/u/{remote_id}",
        published_at=published_at,
    )


def test_recent_author_posts_sorts_filters_limits():
    now = datetime.now(UTC)
    posts = [
        _post("a", now - timedelta(days=10)),
        _post("b", now - timedelta(days=1)),
        _post("c", now - timedelta(days=3), kind="quote"),
        _post("d", now - timedelta(days=2), kind="reply"),
    ]
    out = recent_author_posts(posts, limit=3)
    ids = [p.remote_post_id for p in out]
    assert ids == ["b", "d", "a"]
    assert "c" not in ids


def test_idea_assessment_round_trips():
    result = IdeaAssessment(
        status="ready",
        core_point="Graph 的价值是恢复与重放",
        matched_evidence_ids=["ev_1"],
        draft_id="draft_idea_abc",
        sample="很多 Agent 项目引入 Graph 只是为了画出来……",
    )
    back = IdeaAssessment.model_validate(result.model_dump(mode="json"))
    assert back == result
    assert back.status == "ready"
    assert back.reason_code is None


def test_assess_output_inherits_idea_assessment_fields():
    out = AssessIdeaOutput(
        status="not_ready",
        reason_code="TOO_BROAD",
        reason="没有具体问题",
        reader_problem=None,
        audience=None,
    )
    assert isinstance(out, IdeaAssessment)
    assert out.reason_code == "TOO_BROAD"
    assert out.sample is None
    assert out.matched_evidence_ids == []
