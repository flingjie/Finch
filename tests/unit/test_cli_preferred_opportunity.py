"""Tests for _render_preferred_opportunity (首选机会渲染，规范 §6.1 六问)."""

from finch.cli import (
    _render_opportunity_assessment_coverage,
    _render_preferred_opportunity,
    _render_preferred_skips,
)
from finch.opportunities.models import (
    ContributionForm,
    EntryKind,
    EvidenceRef,
    EvidenceTier,
    Opportunity,
    OpportunityStatus,
    Proposal,
)


def _opp(**kw) -> Opportunity:
    data = dict(
        id="opp_person_1",
        person_ref="person_1",
        topic="失败回放",
        entry_kind=EntryKind.DIFFICULTY,
        why_me="与当前回归测试探索直接相关",
        why_continue="作者已保存 trace，可补充边界",
        proposal=Proposal(
            contribution="做一张 trace→最小回放方法卡",
            form=ContributionForm.METHOD_CARD,
            expected_output="含输入/步骤/输出/限制的方法卡",
            scope="第一版只覆盖一个失败案例",
        ),
        open_questions=["时间依赖是否已固定"],
    )
    data.update(kw)
    return Opportunity(**data)


def test_render_preferred_opportunity_covers_the_six_questions():
    text = "\n".join(_render_preferred_opportunity(_opp()))
    assert "首选机会" in text
    assert "状态：proposed" in text
    assert "失败回放" in text
    assert "为什么值得参与" in text
    assert "对方为什么可能接话" in text
    assert "最小贡献" in text
    assert "可见结果" in text
    assert "范围" in text
    assert "待确认" in text


def test_render_preferred_opportunity_omits_missing_sections():
    opp = _opp(proposal=None, open_questions=[])
    text = "\n".join(_render_preferred_opportunity(opp))
    assert "最小贡献" not in text
    assert "待确认" not in text
    assert "首选机会" in text


def test_render_preferred_opportunity_includes_id_and_source():
    opp = _opp(
        thread_ref="https://x.com/alice/status/1",
        evidence_refs=[
            EvidenceRef(
                source_ref="https://x.com/alice/status/1",
                quote="q",
                claim="c",
                tier=EvidenceTier.EXPLICIT,
            )
        ],
    )
    text = "\n".join(_render_preferred_opportunity(opp))
    assert "机会 ID：opp_person_1" in text
    assert "来源：https://x.com/alice/status/1" in text


def test_render_ready_opportunity_uses_reviewable_heading():
    text = "\n".join(
        _render_preferred_opportunity(_opp(status=OpportunityStatus.READY))
    )
    assert "## 成果待审阅" in text
    assert "状态：ready" in text
    assert "## 首选机会" not in text


def test_render_assessment_coverage_includes_skip_reasons():
    from types import SimpleNamespace

    assessments = [
        SimpleNamespace(outcome="skipped", reason="已解决"),
        SimpleNamespace(outcome="skipped", reason="与现有回复重复"),
        SimpleNamespace(outcome="eval_failed", reason="LLM 超时"),
    ]
    lines = _render_opportunity_assessment_coverage(assessments)
    text = "\n".join(lines)
    assert "本次评估 3 位候选人：2 跳过、1 评估失败" in text
    assert "为何未首选：已解决；与现有回复重复；LLM 超时" in text


def test_render_preferred_skips_lists_other_candidates():
    from types import SimpleNamespace

    lines = _render_preferred_skips(
        [
            SimpleNamespace(outcome="skipped", reason="主题过宽"),
            SimpleNamespace(outcome="recommended", reason=""),
        ]
    )
    assert lines[0] == "其他候选未优先：主题过宽"


def test_render_preferred_opportunity_shows_problem_fit_next_action():
    from finch.opportunities.models import Fit, NextAction, Problem

    opp = _opp(
        problem=Problem(
            statement="修改 prompt 后需手工重跑失败任务",
            source_refs=["https://x.com/a/1"],
            evidence_status="author_stated",
        ),
        fit=Fit(
            reason="与 failure replay 实践相关",
            practice_refs=["agent-100-days"],
            problem_refs=[],
        ),
        next_action=NextAction(type="ask", suggestion="问他如何保留失败输入"),
    )
    text = "\n".join(_render_preferred_opportunity(opp))
    assert "问题：修改 prompt 后需手工重跑失败任务（author_stated" in text
    assert "为什么与我有关：与 failure replay 实践相关（实践 agent-100-days；问题 无）" in text
    assert "下一步：ask —— 问他如何保留失败输入" in text


def test_render_preferred_opportunity_omits_new_fields_when_absent():
    text = "\n".join(_render_preferred_opportunity(_opp()))
    assert "问题：" not in text
    assert "为什么与我有关：" not in text
    assert "下一步：" not in text
