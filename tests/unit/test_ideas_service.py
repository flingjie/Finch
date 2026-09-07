"""Tests for IdeaService (Skill 架构 Step 1 领域核心：幂等键 + 状态转换)。"""

import pytest

from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from finch.content.models import DraftKind
from finch.ideas.models import (
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
    IdeaPosition,
    SourceRef,
)
from finch.ideas.service import IdeaService, idea_generation_key


class FakeContentJobRepository:
    """In-memory double for ContentJobRepository (find_by_generation_key / get_job / upsert_job)."""

    def __init__(self) -> None:
        self._by_id: dict[str, ContentJob] = {}
        self._by_generation_key: dict[str, ContentJob] = {}

    def upsert_job(self, job: ContentJob) -> None:
        self._by_id[job.id] = job
        if job.generation_key is not None:
            self._by_generation_key[job.generation_key] = job

    def get_job(self, job_id: str) -> ContentJob | None:
        return self._by_id.get(job_id)

    def find_by_generation_key(self, generation_key: str) -> ContentJob | None:
        return self._by_generation_key.get(generation_key)


def _candidate(**overrides) -> IdeaCandidate:
    data = dict(
        id="idea_abc123",
        origin="commit",
        core_point="Graph 的价值是恢复与重放",
        reader_problem="很多人只把 Graph 当可视化",
        why_worth_saying="它决定失败后能否重放",
        author_position=IdeaPosition(
            claim="Graph 主要价值是恢复与重放",
            decision="用可恢复性评价 Graph",
            tradeoff="需要持久化状态",
            status="proposed",
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
        recommended_format="original",
        generator=IdeaGenerator(skill="commit-to-idea", version="0.1.0"),
    )
    data.update(overrides)
    return IdeaCandidate(**data)


def _service() -> tuple[IdeaService, FakeContentJobRepository]:
    repo = FakeContentJobRepository()
    return IdeaService(repo), repo


# ---- idea_generation_key ----


def test_idea_generation_key_is_deterministic():
    a = idea_generation_key("commit-to-idea", "0.1.0", "commit:abc123", "fp")
    b = idea_generation_key("commit-to-idea", "0.1.0", "commit:abc123", "fp")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_idea_generation_key_sensitive_to_each_field():
    base = ("commit-to-idea", "0.1.0", "commit:abc123", "fp")
    variants = [
        ("other-skill", "0.1.0", "commit:abc123", "fp"),
        ("commit-to-idea", "0.2.0", "commit:abc123", "fp"),
        ("commit-to-idea", "0.1.0", "post:x", "fp"),
        ("commit-to-idea", "0.1.0", "commit:abc123", "other-fp"),
    ]
    base_key = idea_generation_key(*base)
    assert all(idea_generation_key(*v) != base_key for v in variants)


# ---- create_candidate ----


def test_create_candidate_builds_job():
    svc, _ = _service()
    job = svc.create_candidate(_candidate())
    assert job.id == f"idea_{job.content_fingerprint[:8]}"
    assert job.origin == "commit"
    assert job.status == ContentJobStatus.PROPOSED
    assert job.generator_name == "commit-to-idea"
    assert job.generator_version == "0.1.0"
    assert job.core_message == "Graph 的价值是恢复与重放"
    assert job.why_now == "它决定失败后能否重放"
    assert job.reader_problem == "很多人只把 Graph 当可视化"
    assert job.audience == ""
    assert job.recommended_format == DraftKind.ORIGINAL
    assert job.source_card_ids == []
    assert job.success_criteria == []
    assert job.generation_key is not None
    assert job.idea_candidate_json is not None
    assert job.author_position is not None
    assert job.author_position.claim == "Graph 主要价值是恢复与重放"
    assert job.author_position.change_mind_if is None


def test_create_candidate_maps_reply_and_thread_to_reply():
    svc, _ = _service()
    assert svc.create_candidate(_candidate(recommended_format="reply")).recommended_format == (
        DraftKind.REPLY
    )
    assert svc.create_candidate(_candidate(recommended_format="thread")).recommended_format == (
        DraftKind.REPLY
    )


def test_create_candidate_is_idempotent_by_generation_key():
    svc, repo = _service()
    first = svc.create_candidate(_candidate())
    second = svc.create_candidate(_candidate())
    assert first.id == second.id
    assert len(repo._by_id) == 1


def test_create_candidate_distinguishes_source_refs_in_key():
    svc, _ = _service()
    a = svc.create_candidate(_candidate())
    b = svc.create_candidate(
        _candidate(source_refs=[SourceRef(type="commit", ref="zzz", summary="别的来源")])
    )
    # 生成键区分来源；注意 id 仅由 core_point 决定（既定 id 方案），同核心主张会共享 id。
    assert a.generation_key != b.generation_key
    assert a.id == b.id


# ---- confirm_position ----


def test_confirm_position_moves_proposed_to_confirmed():
    svc, _ = _service()
    job = svc.create_candidate(_candidate())
    confirmed = svc.confirm_position(job.id)
    assert confirmed.status == ContentJobStatus.CONFIRMED
    candidate = IdeaCandidate.model_validate_json(confirmed.idea_candidate_json)
    assert candidate.author_position.status == "confirmed"


def test_confirm_position_illegal_from_confirmed():
    svc, _ = _service()
    job = svc.confirm_position(svc.create_candidate(_candidate()).id)
    with pytest.raises(ValueError):
        svc.confirm_position(job.id)


# ---- require_confirmed ----


def test_require_confirmed_raises_when_not_confirmed():
    svc, _ = _service()
    job = svc.create_candidate(_candidate())
    with pytest.raises(ValueError, match="needs_confirmation") as excinfo:
        svc.require_confirmed(job.id)
    assert job.id in str(excinfo.value)


def test_require_confirmed_raises_when_missing():
    svc, _ = _service()
    with pytest.raises(ValueError, match="needs_confirmation") as excinfo:
        svc.require_confirmed("idea_missing")
    assert "idea_missing" in str(excinfo.value)


def test_require_confirmed_returns_job_when_confirmed():
    svc, _ = _service()
    job = svc.confirm_position(svc.create_candidate(_candidate()).id)
    assert svc.require_confirmed(job.id) == job


# ---- revise_position ----


def test_revise_position_updates_job_and_embedded_json():
    svc, _ = _service()
    job = svc.create_candidate(_candidate())
    new_position = IdeaPosition(
        claim="新主张",
        decision="新决策",
        tradeoff="新权衡",
        status="proposed",
    )
    revised = svc.revise_position(job.id, new_position)
    assert revised.status == ContentJobStatus.PROPOSED  # 不改状态
    assert revised.author_position == AuthorPosition(
        claim="新主张",
        decision="新决策",
        tradeoff="新权衡",
        change_mind_if=None,
    )
    candidate = IdeaCandidate.model_validate_json(revised.idea_candidate_json)
    assert candidate.author_position == new_position


def test_revise_position_legal_from_confirmed():
    svc, _ = _service()
    job = svc.confirm_position(svc.create_candidate(_candidate()).id)
    revised = svc.revise_position(
        job.id,
        IdeaPosition(claim="c", decision="d", tradeoff="t", status="confirmed"),
    )
    assert revised.status == ContentJobStatus.CONFIRMED
    candidate = IdeaCandidate.model_validate_json(revised.idea_candidate_json)
    assert candidate.author_position.status == "confirmed"


# ---- mark_drafted ----


def test_mark_drafted_confirmed_to_drafted():
    svc, _ = _service()
    job = svc.confirm_position(svc.create_candidate(_candidate()).id)
    drafted = svc.mark_drafted(job.id)
    assert drafted.status == ContentJobStatus.DRAFTED


def test_mark_drafted_illegal_from_proposed():
    svc, _ = _service()
    job = svc.create_candidate(_candidate())
    with pytest.raises(ValueError):
        svc.mark_drafted(job.id)


# ---- skip ----


def test_skip_sets_reject_reason():
    svc, _ = _service()
    job = svc.create_candidate(_candidate())
    skipped = svc.skip(job.id, "没有新意")
    assert skipped.status == ContentJobStatus.SKIPPED
    assert skipped.reject_reason == "没有新意"


def test_skip_illegal_from_drafted():
    svc, _ = _service()
    job = svc.mark_drafted(svc.confirm_position(svc.create_candidate(_candidate()).id).id)
    with pytest.raises(ValueError):
        svc.skip(job.id, "late")
