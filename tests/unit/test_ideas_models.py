"""Tests for the IdeaCandidate domain contract (Skill 架构领域核心)."""

import pytest
from pydantic import ValidationError

from finch.content.jobs import AuthorPosition
from finch.content.models import RecommendedFormat
from finch.ideas.models import (
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
    SourceRef,
)


def _candidate(**overrides) -> IdeaCandidate:
    data = dict(
        id="idea_abc123",
        origin="commit",
        core_point="Graph 的价值是恢复与重放",
        reader_problem="很多人只把 Graph 当可视化",
        why_worth_saying="它决定失败后能否重放",
        author_position=AuthorPosition(
            claim="Graph 主要价值是恢复与重放",
            decision="用可恢复性评价 Graph",
            tradeoff="需要持久化状态",
        ),
        source_refs=[
            SourceRef(type="commit", ref="abc123", summary="引入 graph 运行时"),
            SourceRef(type="post", ref="https://x.com/u/1", summary="社区讨论"),
        ],
        boundaries=IdeaBoundaries(
            known=["graph 支持重放"],
            inferred=["用户需要恢复"],
            unknown=["性能影响"],
        ),
        recommended_format=RecommendedFormat.SHORT_POST,
        generator=IdeaGenerator(skill="commit-to-idea", version="0.1.0"),
    )
    data.update(overrides)
    return IdeaCandidate(**data)


def test_idea_candidate_round_trip():
    candidate = _candidate()
    back = IdeaCandidate.model_validate(candidate.model_dump(mode="json"))
    assert back == candidate
    assert back.core_point == "Graph 的价值是恢复与重放"
    assert [s.type for s in back.source_refs] == ["commit", "post"]


def test_idea_candidate_json_round_trip():
    candidate = _candidate()
    back = IdeaCandidate.model_validate_json(candidate.model_dump_json())
    assert back == candidate
    assert back.boundaries.unknown == ["性能影响"]


def test_source_ref_type_rejects_invalid():
    with pytest.raises(ValidationError):
        SourceRef(type="video", ref="x", summary="s")


def test_idea_candidate_origin_rejects_invalid():
    with pytest.raises(ValidationError):
        _candidate(origin="llm")


def test_idea_candidate_recommended_format_rejects_invalid():
    with pytest.raises(ValidationError):
        _candidate(recommended_format="story")


def test_idea_boundaries_default_to_empty():
    b = IdeaBoundaries()
    assert b.known == []
    assert b.inferred == []
    assert b.unknown == []


def test_idea_candidate_requires_author_position_and_generator():
    with pytest.raises(ValidationError):
        IdeaCandidate(
            id="i1",
            origin="user",
            core_point="p",
            reader_problem="rp",
            why_worth_saying="w",
            source_refs=[],
            boundaries=IdeaBoundaries(),
            recommended_format=RecommendedFormat.REPLY,
        )
