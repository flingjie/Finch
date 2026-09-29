"""Tests for 发散相关数据模型（IdeaAngle / IdeaExploration / FactBundle）。"""

from datetime import datetime

from finch.ideas.models import (
    FactBundle,
    IdeaAngle,
    IdeaBoundaries,
    IdeaExploration,
    IdeaGenerator,
    RejectedAngle,
    Selection,
    SourceRef,
)


def _angle(index: int = 1, **overrides) -> IdeaAngle:
    data = dict(
        index=index,
        core_point="小型重复提取应考虑批处理",
        reader_situation="有人把一批小提取任务分发给多个进程",
        reader_takeaway="可尝试批处理降低进程启动开销",
        takeaway_kind="method",
        evidence_support="observed_this_run",
        counterexample_or_limit="批处理会放大单次失败影响",
        source_refs=[SourceRef(type="commit", ref="https://github.com/a/b/commit/x", summary="m")],
    )
    data.update(overrides)
    return IdeaAngle(**data)


def _bundle() -> FactBundle:
    return FactBundle(
        facts=["启动开销使整体耗时很长"],
        source_refs=[SourceRef(type="commit", ref="https://github.com/a/b/commit/x", summary="m")],
        boundaries=IdeaBoundaries(),
        evidence_status="observed",
        origin="practice",
        source_kind="commit",
    )


def test_fact_bundle_round_trip():
    b = _bundle()
    assert FactBundle.model_validate(b.model_dump(mode="json")) == b


def test_idea_angle_round_trip():
    a = _angle()
    assert IdeaAngle.model_validate(a.model_dump(mode="json")) == a


def test_exploration_round_trip_with_selection():
    e = IdeaExploration(
        id="expl_abc12345",
        origin="practice",
        source_kind="commit",
        evidence_status="observed",
        facts=["启动开销使整体耗时很长"],
        source_refs=[SourceRef(type="commit", ref="https://github.com/a/b/commit/x", summary="m")],
        boundaries=IdeaBoundaries(known=["启动开销使整体耗时很长"]),
        angles=[_angle()],
        rejected_angles=[RejectedAngle(index=2, core_point="并发先拆推理时间", reason="无来源")],
        recommended_index=1,
        recommendation_reason="给处理批量任务的人一个可验证的改法",
        selections=[Selection(index=1, job_id="idea_x", at=datetime(2026, 9, 29))],
        generator=IdeaGenerator(skill="idea-discovery", version="2.0.0"),
    )
    assert IdeaExploration.model_validate(e.model_dump(mode="json")) == e
    assert e.angles[0].takeaway_kind == "method"


def test_idea_candidate_accepts_new_optional_fields():
    from finch.content.jobs import AuthorPosition
    from finch.content.models import RecommendedFormat
    from finch.ideas.models import IdeaCandidate

    c = IdeaCandidate(
        id="idea_x",
        origin="practice",
        core_point="小型重复提取应考虑批处理",
        reader_problem="有人把一批小提取任务分发给多个进程",
        why_worth_saying="可尝试批处理降低进程启动开销",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        source_refs=[],
        boundaries=IdeaBoundaries(),
        recommended_format=RecommendedFormat.SHORT_POST,
        generator=IdeaGenerator(skill="idea-discovery", version="2.0.0"),
        reader_situation="s",
        reader_takeaway="t",
        takeaway_kind="method",
        evidence_support="observed_this_run",
        counterexample_or_limit="limit",
    )
    assert c.evidence_support == "observed_this_run"
    assert c.takeaway_kind == "method"
