"""Tests for _render_preferred_opportunity (首选机会渲染，规范 §6.1 六问)."""

from finch.cli import _render_preferred_opportunity
from finch.opportunities.models import (
    ContributionForm,
    EntryKind,
    EvidenceRef,
    EvidenceTier,
    Opportunity,
    Proposal,
)


def _opp() -> Opportunity:
    return Opportunity(
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


def test_render_preferred_opportunity_covers_the_six_questions():
    text = "\n".join(_render_preferred_opportunity(_opp()))
    assert "首选机会" in text
    assert "失败回放" in text
    assert "为什么值得参与" in text
    assert "对方为什么可能接话" in text
    assert "最小贡献" in text
    assert "可见结果" in text
    assert "范围" in text
    assert "待确认" in text


def test_render_preferred_opportunity_omits_missing_sections():
    opp = _opp().model_copy(update={"proposal": None, "open_questions": []})
    text = "\n".join(_render_preferred_opportunity(opp))
    assert "最小贡献" not in text
    assert "待确认" not in text
    assert "首选机会" in text


def test_render_preferred_opportunity_includes_id_and_source():
    opp = _opp().model_copy(
        update={
            "thread_ref": "https://x.com/alice/status/1",
            "evidence_refs": [
                EvidenceRef(
                    source_ref="https://x.com/alice/status/1",
                    quote="q",
                    claim="c",
                    tier=EvidenceTier.EXPLICIT,
                )
            ],
        }
    )
    text = "\n".join(_render_preferred_opportunity(opp))
    assert "机会 ID：opp_person_1" in text
    assert "来源：https://x.com/alice/status/1" in text
