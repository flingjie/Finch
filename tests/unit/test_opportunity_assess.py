"""Tests for opportunity assessment (首选机会判断：LLM 产出 why_me/why_continue/proposal)."""

from finch.opportunities.assess import (
    OpportunityDraft,
    assess_opportunity,
    build_opportunity,
)
from finch.opportunities.models import (
    ContributionForm,
    EntryKind,
    EvidenceRef,
    EvidenceTier,
    Opportunity,
)


class FakeRunner:
    def __init__(self, ret: OpportunityDraft | None = None, exc: Exception | None = None):
        self.ret = ret
        self.exc = exc
        self.calls = 0
        self.last_prompt: str | None = None

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_prompt = prompt
        if self.exc is not None:
            raise self.exc
        return self.ret


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
        open_questions=["时间依赖是否已固定"],
        recommend=True,
    )
    data.update(kw)
    return OpportunityDraft(**data)


def _kwargs() -> dict:
    return dict(
        peer_id="peer_1",
        display_name="alice",
        platform="x",
        current_work="failure replay",
        why_relevant="regression tests",
        their_artifacts_json='[{"artifact_id": "a1"}]',
        user_context="回归测试",
    )


def test_assess_opportunity_returns_draft():
    runner = FakeRunner(_draft())
    out = assess_opportunity(runner, **_kwargs())
    assert out.topic == "失败回放"
    assert out.entry_kind == EntryKind.DIFFICULTY
    assert out.recommend is True
    assert runner.calls == 1


def test_assess_opportunity_renders_context_in_prompt():
    runner = FakeRunner(_draft())
    assess_opportunity(runner, **_kwargs())
    p = runner.last_prompt or ""
    assert "peer_1" in p
    assert "alice" in p
    assert "failure replay" in p
    assert "regression tests" in p
    assert "回归测试" in p


def test_assess_opportunity_falls_back_on_llm_error():
    runner = FakeRunner(exc=RuntimeError("boom"))
    out = assess_opportunity(runner, **_kwargs())
    assert out.recommend is False
    assert out.skip_reason


def test_build_opportunity_from_recommend_draft():
    opp = build_opportunity(_draft(), opportunity_id="opp_1", person_ref="person_1")
    assert isinstance(opp, Opportunity)
    assert opp.id == "opp_1"
    assert opp.person_ref == "person_1"
    assert opp.entry_kind == EntryKind.DIFFICULTY
    assert opp.why_me == "与当前回归测试探索直接相关"
    assert opp.proposal is not None
    assert opp.proposal.contribution == "做一张 trace→最小回放方法卡"
    assert opp.proposal.form == ContributionForm.METHOD_CARD
    assert opp.open_questions == ["时间依赖是否已固定"]


def test_build_opportunity_none_when_not_recommend():
    assert build_opportunity(_draft(recommend=False), opportunity_id="o") is None


def test_build_opportunity_none_when_no_contribution():
    assert build_opportunity(_draft(contribution="  "), opportunity_id="o") is None


def test_build_opportunity_carries_thread_ref_and_evidence_refs():
    draft = _draft(
        thread_ref="https://x.com/alice/status/1",
        evidence_refs=[
            EvidenceRef(
                source_ref="https://x.com/alice/status/1",
                quote="同一任务重跑结果不同",
                claim="失败可复现",
                tier=EvidenceTier.EXPLICIT,
            )
        ],
    )
    opp = build_opportunity(draft, opportunity_id="opp_1", person_ref="person_1")
    assert opp.thread_ref == "https://x.com/alice/status/1"
    assert len(opp.evidence_refs) == 1
    assert opp.evidence_refs[0].tier == EvidenceTier.EXPLICIT


def test_assess_opportunity_marks_eval_failed_on_error():
    runner = FakeRunner(exc=RuntimeError("boom"))
    out = assess_opportunity(runner, **_kwargs())
    assert out.eval_failed is True
    assert out.recommend is False


def test_assess_opportunity_renders_user_practices_block():
    runner = FakeRunner(_draft())
    assess_opportunity(
        runner,
        **_kwargs(),
        user_practices="- [agent-100-days] (sourced) agent engineering: 100 天路径",
    )
    p = runner.last_prompt or ""
    assert "## User real practices (confirmed, citeable)" in p
    assert "[agent-100-days]" in p


def test_assess_opportunity_empty_practices_renders_none():
    runner = FakeRunner(_draft())
    assess_opportunity(runner, **_kwargs())
    p = runner.last_prompt or ""
    idx = p.index("## User real practices (confirmed, citeable)")
    assert "(none)" in p[idx : idx + 400]
