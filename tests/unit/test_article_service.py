"""ArticleAnalysisService：analyze 覆盖确定性字段。"""

from finch.article.models import (
    ArticleReport,
    AudienceChange,
    Effectiveness,
    ExpressionTask,
    TechniqueBreakdown,
    TransferableMethod,
)
from finch.article.service import ArticleAnalysisService
from finch.article.source_resolver import ResolvedSource


class _Runner:
    def __init__(self, ret):
        self.ret = ret
        self.last_prompt = None

    def run(self, prompt, output_model, **kw):
        self.last_prompt = prompt
        return self.ret


def _source():
    return ResolvedSource(
        body="测试通过，但问题没解决。记忆不是聊天记录。",
        content_hash="abc123",
        sample_size=1,
        source_type="file",
        source_ref="a.md",
    )


def _raw_report(**overrides) -> ArticleReport:
    data = dict(
        id="model-set",
        source_type="url",
        content_hash="model-hash",
        expression_task=ExpressionTask(
            topic="记忆", primary_task="解释概念", inferred=True
        ),
        audience_change=AudienceChange(
            who="开发者", before="混淆", after="区分", fit_check="ok"
        ),
        techniques=[
            TechniqueBreakdown(
                excerpt="测试通过，但问题没解决",
                method="反常结果先行",
                reader_effect="先感到问题",
            )
        ],
        effectiveness=Effectiveness(
            clarity="清楚",
            concreteness="有场景",
            credibility="区分推断",
            actionability="不适用：理解即可",
        ),
        transferable_methods=[
            TransferableMethod(
                method="a", why_effective_here="w", when_to_use="u", mini_exercise="e"
            ),
            TransferableMethod(
                method="b", why_effective_here="w", when_to_use="u", mini_exercise="e"
            ),
        ],
    )
    data.update(overrides)
    return ArticleReport(**data)


def test_analyze_overrides_deterministic_fields():
    runner = _Runner(_raw_report())
    report = ArticleAnalysisService(runner).analyze(_source())
    assert report.id != "model-set"
    assert report.id.startswith("article_")
    assert report.source_type == "file"
    assert report.source_ref == "a.md"
    assert report.content_hash == "abc123"
    assert report.expression_task.inferred is True
    assert report.effectiveness.actionability.startswith("不适用")
    assert "测试通过" in (runner.last_prompt or "")


def test_analyze_id_stable_for_same_hash():
    svc = ArticleAnalysisService(_Runner(_raw_report()))
    a = svc.analyze(_source())
    b = svc.analyze(_source())
    assert a.id == b.id


def test_analyze_prompt_includes_sample_size_and_style():
    runner = _Runner(_raw_report())
    src = _source().model_copy(update={"sample_size": 3})
    ArticleAnalysisService(runner).analyze(src)
    prompt = runner.last_prompt or ""
    assert "Sample count\n3" in prompt
    assert "reader_relationship" in prompt
    assert "clarity_cost_reductions" in prompt
