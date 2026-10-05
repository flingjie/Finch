"""render_opportunity_questions：三问映射 + 字段缺失回退。"""

from finch.opportunities.models import (
    ContributionForm,
    Fit,
    Opportunity,
    Problem,
    Proposal,
)
from finch.opportunities.render import render_opportunity_questions


def _opp(**kw) -> Opportunity:
    base = dict(
        id="opp_1",
        topic="工具超时误报成功",
        problem=Problem(
            statement="工具超时后误报成功", evidence_status="author_stated"
        ),
        fit=Fit(
            reason="你在 Agent-100-Days 也踩过",
            practice_refs=["agent-100-days"],
            problem_refs=["problem_abc"],
        ),
        proposal=Proposal(
            contribution="一张错误分类后重试的方法卡",
            form=ContributionForm.METHOD_CARD,
            expected_output="决策表",
        ),
    )
    base.update(kw)
    return Opportunity(**base)


def test_three_questions_rendered():
    out = render_opportunity_questions(_opp())
    assert "他解决什么" in out and "工具超时后误报成功" in out
    assert "与你什么相关" in out and "agent-100-days" in out and "problem_abc" in out
    assert "你能贡献什么" in out and "方法卡" in out
    assert "method_card" in out and "决策表" in out


def test_fallback_when_fields_missing():
    out = render_opportunity_questions(_opp(problem=None, fit=None, proposal=None))
    assert "他解决什么" in out and "工具超时误报成功" in out  # falls back to topic
    assert "与你什么相关" in out
    assert "你能贡献什么" in out and "待准备" in out
