"""Tests for discover_preferred_opportunity (把候选转成首选机会并落库)."""

from datetime import UTC, datetime

from finch.opportunities.assess import OpportunityDraft
from finch.opportunities.discover import (
    discover_preferred_opportunity,
    discover_preferred_opportunity_outcome,
    opportunity_context_fingerprint,
    render_artifacts_json,
)
from finch.opportunities.models import ContributionForm, EntryKind
from finch.opportunities.repository import OpportunityRepository
from finch.opportunities.service import OpportunityService
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.storage.workspace import Workspace


class FakeRunner:
    def __init__(self, draft: OpportunityDraft):
        self.draft = draft
        self.calls = 0
        self.last_prompt: str | None = None

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.draft


def _draft(**kw) -> OpportunityDraft:
    data = dict(
        topic="失败回放",
        entry_kind=EntryKind.DIFFICULTY,
        why_me="与当前回归测试探索直接相关",
        why_continue="作者已保存 trace，可补充边界",
        contribution="做一张 trace→最小回放方法卡",
        form=ContributionForm.METHOD_CARD,
        expected_output="含输入/步骤/输出/限制的方法卡",
        scope="第一版只覆盖一个失败案例",
        recommend=True,
    )
    data.update(kw)
    return OpportunityDraft(**data)


def _artifact(
    artifact_id: str = "a1", title: str = "failure replay", text: str = "同一任务重跑结果不同"
) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id,
        source=Source.TWITTER,
        source_type="post",
        source_id="s1",
        author_identity=AuthorIdentity(platform="x", external_id="u1", handle="alice"),
        title=title,
        text=text,
        retrieved_at=datetime.now(UTC),
    )


def _service(tmp_path) -> OpportunityService:
    return OpportunityService(OpportunityRepository(Workspace(tmp_path)))


def _kwargs(tmp_path) -> dict:
    return dict(
        runner=FakeRunner(_draft()),
        peer_id="peer_1",
        display_name="alice",
        platform="x",
        current_work="failure replay",
        why_relevant="regression tests",
        person_ref="person_1",
        artifacts=[_artifact()],
        service=_service(tmp_path),
    )


def test_discovers_and_creates_preferred_opportunity(tmp_path):
    opp = discover_preferred_opportunity(**_kwargs(tmp_path))
    assert opp is not None
    assert opp.id.startswith("opp_person_1_")
    assert opp.person_ref == "person_1"
    assert opp.entry_kind == EntryKind.DIFFICULTY
    assert opp.proposal is not None
    assert opp.proposal.contribution == "做一张 trace→最小回放方法卡"
    assert _service(tmp_path).get(opp.id) is not None


def test_idempotent_returns_existing_without_reassessing(tmp_path):
    kwargs = _kwargs(tmp_path)
    first = discover_preferred_opportunity(**kwargs)
    calls = kwargs["runner"].calls
    second = discover_preferred_opportunity(**kwargs)
    assert second == first
    assert kwargs["runner"].calls == calls  # 命中已有，未重新调 LLM


def test_returns_none_when_not_recommended(tmp_path):
    kwargs = _kwargs(tmp_path)
    kwargs["runner"] = FakeRunner(_draft(recommend=False, skip_reason="已解决"))
    assert discover_preferred_opportunity(**kwargs) is None
    assert _service(tmp_path).repo.list_all() == []


def test_renders_artifact_text_in_prompt(tmp_path):
    kwargs = _kwargs(tmp_path)
    kwargs["artifacts"] = [_artifact(text="重跑结果不同")]
    discover_preferred_opportunity(**kwargs)
    assert "重跑结果不同" in kwargs["runner"].last_prompt


def test_render_artifacts_json_truncates_long_text():
    out = render_artifacts_json([_artifact(text="x" * 1000)])
    assert '"title": "failure replay"' in out
    assert "…" in out


def test_id_encodes_discussion_fingerprint_not_just_person(tmp_path):
    kwargs = _kwargs(tmp_path)
    first = discover_preferred_opportunity(**kwargs)
    assert first is not None

    # 换讨论材料（artifact 文本变化）→ 新指纹 → 重新评估并生成新机会。
    changed = dict(kwargs)
    changed["artifacts"] = [_artifact(text="另一个完全不同的讨论话题")]
    second = discover_preferred_opportunity(**changed)
    assert second is not None
    assert second.id != first.id
    assert second.person_ref == "person_1"
    assert kwargs["runner"].calls == 2


def test_closed_or_parked_opportunity_does_not_reappear_as_preferred(tmp_path):
    kwargs = _kwargs(tmp_path)
    service = _service(tmp_path)
    opp = discover_preferred_opportunity(**kwargs)
    assert opp is not None
    service.close(opp.id)

    again = discover_preferred_opportunity(**kwargs)
    assert again is None


def test_outcome_distinguishes_recommended_skipped_and_eval_failed(tmp_path):
    kwargs = _kwargs(tmp_path)
    assert (
        discover_preferred_opportunity_outcome(**kwargs).outcome == "recommended"
    )

    skipped_tmp = tmp_path / "skipped"
    skipped_tmp.mkdir()
    skipped = _kwargs(skipped_tmp)
    skipped["runner"] = FakeRunner(_draft(recommend=False, skip_reason="已解决"))
    assert discover_preferred_opportunity_outcome(**skipped).outcome == "skipped"

    failed_tmp = tmp_path / "failed"
    failed_tmp.mkdir()
    failed = _kwargs(failed_tmp)

    class Boom:
        def run(self, prompt, output_model, **kw):
            raise RuntimeError("boom")

    failed["runner"] = Boom()
    assert discover_preferred_opportunity_outcome(**failed).outcome == "eval_failed"


def test_skipped_assessment_is_cached_and_not_reassessed(tmp_path):
    from finch.opportunities.repository import SkipAssessmentRepository

    kwargs = _kwargs(tmp_path)
    kwargs["skips"] = SkipAssessmentRepository(Workspace(tmp_path))
    kwargs["runner"] = FakeRunner(_draft(recommend=False, skip_reason="已解决"))
    first = discover_preferred_opportunity_outcome(**kwargs)
    assert first.outcome == "skipped"
    assert kwargs["runner"].calls == 1

    second = discover_preferred_opportunity_outcome(**kwargs)
    assert second.outcome == "skipped"
    assert second.reason == "已解决"
    assert kwargs["runner"].calls == 1  # 命中 skip 缓存，未重新调 LLM


def test_eval_failed_is_not_cached(tmp_path):
    from finch.opportunities.repository import SkipAssessmentRepository

    kwargs = _kwargs(tmp_path)
    kwargs["skips"] = SkipAssessmentRepository(Workspace(tmp_path))

    class Boom:
        def run(self, prompt, output_model, **kw):
            raise RuntimeError("boom")

    kwargs["runner"] = Boom()
    assert discover_preferred_opportunity_outcome(**kwargs).outcome == "eval_failed"
    # 换一个仍失败的 runner，确认没有被误缓存成 skipped。
    assert discover_preferred_opportunity_outcome(**kwargs).outcome == "eval_failed"


def test_context_fingerprint_changes_with_user_question(tmp_path):
    base = dict(
        person_ref="person_1",
        current_work="w",
        why_relevant="r",
        artifacts=[_artifact()],
        user_context="",
    )
    a = opportunity_context_fingerprint(**base)
    b = opportunity_context_fingerprint(**{**base, "user_context": "新问题"})
    assert a != b


def test_context_fingerprint_changes_with_user_practices():
    base = dict(
        person_ref="person_1",
        current_work="w",
        why_relevant="r",
        artifacts=[_artifact()],
        user_context="",
    )
    a = opportunity_context_fingerprint(**base)
    b = opportunity_context_fingerprint(**{**base, "user_practices": "- [x] (sourced) d: c"})
    assert a != b
    # 默认值不改变既有指纹（回归保护）
    assert a == opportunity_context_fingerprint(**{**base, "user_practices": ""})


def test_context_fingerprint_none_marker_matches_empty():
    base = dict(
        person_ref="person_1",
        current_work="w",
        why_relevant="r",
        artifacts=[_artifact()],
        user_context="",
    )
    assert opportunity_context_fingerprint(**base) == opportunity_context_fingerprint(
        **{**base, "user_practices": "(none)"}
    )
