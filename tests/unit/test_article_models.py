"""article_analysis 数据模型。"""

import pytest
from pydantic import ValidationError

from finch.article.models import (
    ArticleReport,
    AudienceChange,
    ClarityCostReduction,
    Effectiveness,
    ExpressionTask,
    TechniqueBreakdown,
    TransferableMethod,
)


def _methods(n: int = 2) -> list[TransferableMethod]:
    return [
        TransferableMethod(
            method=f"m{i}",
            why_effective_here="why",
            when_to_use="when",
            mini_exercise="ex",
        )
        for i in range(n)
    ]


def test_article_report_round_trip_and_no_total():
    report = ArticleReport(
        expression_task=ExpressionTask(
            topic="Agent 记忆",
            primary_task="解释记忆不等于聊天记录",
            secondary_tasks=["引发讨论"],
            inferred=True,
        ),
        audience_change=AudienceChange(
            who="做 Agent 的开发者",
            before="把日志当记忆",
            after="区分状态与记忆设计",
            fit_check="术语适合有工程背景的读者",
        ),
        techniques=[
            TechniqueBreakdown(
                excerpt="测试通过，但问题没解决",
                method="先给反常结果再解释",
                reader_effect="先感到问题再接受概念",
                caveat="未标明假设时读者可能当真",
            )
        ],
        effectiveness=Effectiveness(
            clarity="核心对比清楚",
            concreteness="有场景但缺数字",
            credibility="区分了事实与推断",
            actionability="不适用：目的是理解而非行动",
        ),
        transferable_methods=_methods(2),
        limitations=["单篇推断意图"],
    )
    assert report.expression_task.inferred is True
    assert "total" not in ArticleReport.model_fields
    assert "total" not in report.model_dump()


def test_transferable_methods_must_be_two_or_three():
    base = dict(
        expression_task=ExpressionTask(topic="t", primary_task="p"),
        audience_change=AudienceChange(
            who="w", before="b", after="a", fit_check="f"
        ),
        effectiveness=Effectiveness(
            clarity="c", concreteness="c", credibility="c", actionability="n/a"
        ),
    )
    with pytest.raises(ValidationError):
        ArticleReport(**base, transferable_methods=_methods(1))
    with pytest.raises(ValidationError):
        ArticleReport(**base, transferable_methods=_methods(4))
    ok = ArticleReport(**base, transferable_methods=_methods(3))
    assert len(ok.transferable_methods) == 3


def _base_kwargs():
    return dict(
        expression_task=ExpressionTask(topic="t", primary_task="p"),
        audience_change=AudienceChange(who="w", before="b", after="a", fit_check="f"),
        effectiveness=Effectiveness(
            clarity="c", concreteness="c", credibility="c", actionability="n/a"
        ),
        transferable_methods=_methods(2),
    )


def test_clarity_cost_reductions_max_three():
    items = [
        ClarityCostReduction(
            excerpt=f"e{i}",
            method="m",
            reader_effect="r",
            mini_exercise="ex",
            rule_id="CL01",
        )
        for i in range(3)
    ]
    report = ArticleReport(**_base_kwargs(), clarity_cost_reductions=items)
    assert len(report.clarity_cost_reductions) == 3


def test_clarity_cost_reductions_rejects_four():
    items = [
        ClarityCostReduction(excerpt=f"e{i}", method="m", reader_effect="r", mini_exercise="ex")
        for i in range(4)
    ]
    with pytest.raises(ValidationError):
        ArticleReport(**_base_kwargs(), clarity_cost_reductions=items)


def test_clarity_cost_reductions_default_empty():
    report = ArticleReport(**_base_kwargs())
    assert report.clarity_cost_reductions == []
