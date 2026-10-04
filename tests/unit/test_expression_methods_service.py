"""ExpressionMethodService：from_report / save / merge / log."""


from finch.article.models import (
    ArticleReport,
    AudienceChange,
    Effectiveness,
    ExpressionTask,
    TransferableMethod,
)
from finch.article.repository import ArticleReportRepository
from finch.expression_methods.models import (
    MergeCandidate,
    MergeSuggestion,
)
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.expression_methods.service import ExpressionMethodService
from finch.storage.workspace import Workspace


class FakeRunner:
    def __init__(self, suggestion: MergeSuggestion | None = None):
        self.suggestion = suggestion or MergeSuggestion(candidates=[])
        self.prompts: list[str] = []

    def run(self, prompt, output_model, **kw):
        self.prompts.append(prompt)
        if output_model is MergeSuggestion:
            return self.suggestion
        raise AssertionError(output_model)


def _report(tmp_path) -> ArticleReport:
    report = ArticleReport(
        id="article_x",
        source_type="url",
        source_ref="https://example.com/a",
        content_hash="h",
        expression_task=ExpressionTask(topic="t", primary_task="解释"),
        audience_change=AudienceChange(
            who="dev", before="a", after="b", fit_check="ok"
        ),
        effectiveness=Effectiveness(
            clarity="c", concreteness="c", credibility="c", actionability="n/a"
        ),
        transferable_methods=[
            TransferableMethod(
                method="失败开场",
                why_effective_here="先见损失",
                when_to_use="复盘",
                mini_exercise="写失败开头",
            ),
            TransferableMethod(
                method="先结果后机制",
                why_effective_here="降低抽象",
                when_to_use="解释概念",
                mini_exercise="先写后果",
            ),
        ],
    )
    ArticleReportRepository(Workspace(tmp_path)).upsert(report)
    return report


def _svc(tmp_path, runner=None) -> ExpressionMethodService:
    ws = Workspace(tmp_path)
    return ExpressionMethodService(
        ExpressionMethodRepository(ws),
        ArticleReportRepository(ws),
        runner or FakeRunner(),
    )


def test_save_as_new_maps_fields(tmp_path):
    _report(tmp_path)
    m = _svc(tmp_path).save_as_new("article_x", 2)
    assert m.title == "先结果后机制"
    assert m.why_effective == "降低抽象"
    assert m.boundaries == ""
    assert m.sources[0].method_index == 2
    assert m.sources[0].source_ref == "https://example.com/a"
    assert m.sources[0].why_effective_here == "降低抽象"
    assert m.sources[0].content_hash == "h"
    assert ExpressionMethodRepository(Workspace(tmp_path)).get(m.id) is not None


def test_save_as_new_idempotent_same_report_index(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    a = svc.save_as_new("article_x", 1)
    b = svc.save_as_new("article_x", 1)
    assert a.id == b.id
    assert len(ExpressionMethodRepository(Workspace(tmp_path)).list_all()) == 1


def test_save_index_out_of_range(tmp_path):
    _report(tmp_path)
    try:
        _svc(tmp_path).save_as_new("article_x", 9)
    except ValueError as e:
        assert "index" in str(e).casefold()
        return
    raise AssertionError("expected ValueError")


def test_merge_appends_source_idempotent(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    a = svc.save_as_new("article_x", 1)
    b = svc.merge_into(a.id, "article_x", 2)
    assert len(b.sources) == 2
    b2 = svc.merge_into(a.id, "article_x", 2)
    assert len(b2.sources) == 2


def test_suggest_merges_uses_runner(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    existing = svc.save_as_new("article_x", 1)
    runner = FakeRunner(
        MergeSuggestion(
            candidates=[
                MergeCandidate(method_id=existing.id, reason="同为失败开场")
            ]
        )
    )
    svc2 = _svc(tmp_path, runner)
    draft = svc2.from_report(
        ArticleReportRepository(Workspace(tmp_path)).get("article_x"), 1
    )
    # from_report on same index — for suggest use index 2 as "new" candidate
    draft = svc2.from_report(
        ArticleReportRepository(Workspace(tmp_path)).get("article_x"), 2
    )
    sug = svc2.suggest_merges(draft)
    assert sug.candidates[0].method_id == existing.id
    assert runner.prompts


def test_append_practice_log(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    m = svc.save_as_new("article_x", 1)
    m = svc.append_practice_log(m.id, "practice_1", "worth_reuse", note="好用")
    assert m.practice_logs[-1].verdict == "worth_reuse"
    assert m.practice_logs[-1].note == "好用"


def test_resolve_methods_by_id_and_missing(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    m = svc.save_as_new("article_x", 1)
    got = svc.resolve_methods_for_discovery(method_ids=[m.id])
    assert [x.id for x in got] == [m.id]
    try:
        svc.resolve_methods_for_discovery(method_ids=["nope"])
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_resolve_methods_from_report_ephemeral_when_unsaved(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    got = svc.resolve_methods_for_discovery(report_id="article_x")
    assert len(got) == 2
    assert all(g.id.startswith("emethod_ephemeral_") for g in got)
    assert ExpressionMethodRepository(Workspace(tmp_path)).list_all() == []


def test_soft_filter_keeps_when_required_material_matches(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    m = svc.save_as_new("article_x", 1)
    m = m.model_copy(update={"required_material": "启动开销"})
    ExpressionMethodRepository(Workspace(tmp_path)).upsert(m)
    kept = svc.soft_filter_for_facts([m], ["启动开销使整体耗时很长"])
    assert kept == [m]
    dropped = svc.soft_filter_for_facts([m], ["无关事实"])
    # lenient fallback: if all drop, return original list
    assert dropped == [m]
