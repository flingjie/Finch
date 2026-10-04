"""Tests for IdeaDiverger（发散 → 确定性核验 → 可选方法辅助 → 收敛）。"""

from datetime import UTC, datetime

from finch.expression_methods.models import ExpressionMethod
from finch.ideas.divergence import (
    ConvergeOutput,
    DivergeOutput,
    IdeaDiverger,
    MethodAssistAnglePatch,
    MethodAssistDraftNote,
    MethodAssistOutput,
    MethodAssistSelection,
)
from finch.ideas.models import FactBundle, IdeaBoundaries, SourceRef


class FakeRunner:
    def __init__(
        self,
        diverge_out: DivergeOutput,
        converge_out: ConvergeOutput,
        assist_out: MethodAssistOutput | None = None,
    ):
        self.diverge_out = diverge_out
        self.converge_out = converge_out
        self.assist_out = assist_out or MethodAssistOutput()
        self.calls: list[type] = []

    def run(self, prompt, output_model, **kw):
        self.calls.append(output_model)
        if output_model is DivergeOutput:
            return self.diverge_out
        if output_model is MethodAssistOutput:
            return self.assist_out
        if output_model is ConvergeOutput:
            return self.converge_out
        raise AssertionError(f"unexpected model {output_model}")


def _method(mid: str = "emethod_1", **kw) -> ExpressionMethod:
    now = datetime.now(UTC)
    data = dict(
        id=mid,
        title="从失败过程切入",
        why_effective="先见损失",
        when_to_use="复盘",
        mini_exercise="写失败开头",
        created_at=now,
        updated_at=now,
    )
    data.update(kw)
    return ExpressionMethod(**data)


def _bundle() -> FactBundle:
    return FactBundle(
        facts=["并发任务慢", "启动开销使整体耗时很长"],
        source_refs=[
            SourceRef(type="commit", ref="https://github.com/a/b/commit/1", summary="c1"),
            SourceRef(type="commit", ref="https://github.com/a/b/commit/2", summary="c2"),
        ],
        boundaries=IdeaBoundaries(),
        evidence_status="observed",
        origin="practice",
        source_kind="commit",
    )


def _angle(**kw) -> dict:
    data = dict(
        core_point="小型重复提取应考虑批处理",
        reader_situation="有人把一批小提取任务分发给多个进程",
        reader_takeaway="可尝试批处理降低进程启动开销",
        takeaway_kind="method",
        evidence_support="observed_this_run",
        counterexample_or_limit="批处理会放大单次失败影响",
        source_ref_indices=[0, 1],
    )
    data.update(kw)
    return data


def _diverge(*angles) -> DivergeOutput:
    from finch.ideas.divergence import DivergeAngle

    return DivergeOutput(angles=[DivergeAngle(**a) for a in angles])


def test_explore_resolves_sources_and_recommends():
    d = _diverge(_angle(), _angle(core_point="并发任务慢先拆推理时间", source_ref_indices=[0]))
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="可验证的改法")))
    e = svc.explore(_bundle())
    assert len(e.angles) == 2
    assert e.angles[0].index == 1
    assert [s.ref for s in e.angles[0].source_refs] == [
        "https://github.com/a/b/commit/1", "https://github.com/a/b/commit/2",
    ]
    assert e.recommended_index == 1
    assert e.recommendation_reason == "可验证的改法"
    assert e.id.startswith("expl_")


def test_verify_rejects_unresolvable_source():
    d = _diverge(_angle(source_ref_indices=[9]))  # 越界
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="")))
    e = svc.explore(_bundle())
    assert e.angles == []
    assert e.recommended_index is None
    assert e.rejected_angles[0].reason == "引用来源不存在"


def test_verify_rejects_no_source():
    d = _diverge(_angle(source_ref_indices=[]))
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="")))
    e = svc.explore(_bundle())
    assert e.angles == []
    assert e.rejected_angles[0].reason == "无来源"


def test_verify_rejects_duplicate_core_point():
    d = _diverge(_angle(), _angle())  # 同 core_point
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="")))
    e = svc.explore(_bundle())
    assert len(e.angles) == 1
    assert e.rejected_angles[0].reason == "与另一角度重复"


def test_verify_demotes_observed_when_externally_reported():
    bundle = _bundle().model_copy(update={"evidence_status": "externally_reported"})
    d = _diverge(_angle(evidence_support="observed_this_run"))
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="")))
    e = svc.explore(bundle)
    assert e.angles[0].evidence_support == "unverified_general"


def test_converge_rejects_all_when_no_index():
    d = _diverge(_angle())
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=None, reason="无迁移价值")))
    e = svc.explore(_bundle())
    assert e.angles == []
    assert e.recommended_index is None
    assert e.rejected_angles[0].reason == "无可迁移价值"


def test_converge_validates_recommended_index_against_angle_index():
    # 角度 1 无来源 → 淘汰；角度 2 存活（原 index 保留为 2，出现间隙）。
    d = _diverge(
        _angle(source_ref_indices=[]),
        _angle(core_point="并发任务慢先拆推理时间", source_ref_indices=[0]),
    )
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=2, reason="r")))
    e = svc.explore(_bundle())
    assert e.recommended_index == 2
    assert len(e.angles) == 1
    assert e.angles[0].index == 2


def test_explore_same_input_same_id():
    d = _diverge(_angle())
    out = ConvergeOutput(recommended_index=1, reason="r")
    a = IdeaDiverger(FakeRunner(d, out)).explore(_bundle())
    b = IdeaDiverger(FakeRunner(d, out)).explore(_bundle())
    assert a.id == b.id


def test_diverge_output_accepts_bare_array():
    data = [_angle()]
    parsed = DivergeOutput.model_validate(data)
    assert len(parsed.angles) == 1
    assert parsed.angles[0].core_point == "小型重复提取应考虑批处理"


def test_diverge_output_accepts_wrapped_shape():
    parsed = DivergeOutput.model_validate({"angles": [_angle()]})
    assert len(parsed.angles) == 1


def test_explore_without_methods_skips_assist():
    d = _diverge(_angle())
    runner = FakeRunner(d, ConvergeOutput(recommended_index=1, reason="r"))
    e = IdeaDiverger(runner).explore(_bundle())
    assert MethodAssistOutput not in runner.calls
    assert e.method_selections == []
    assert e.generator.version == "2.1.0"


def test_explore_with_methods_runs_assist_and_patches():
    d = _diverge(_angle())
    assist = MethodAssistOutput(
        method_selections=[
            MethodAssistSelection(
                method_id="emethod_1",
                fit_reason="失败开场贴合慢任务",
                material_refs=["并发任务慢"],
                missing_requirements=["各阶段耗时"],
                use_as="idea_angle",
            )
        ],
        angle_patches=[
            MethodAssistAnglePatch(
                angle_index=1,
                method_id="emethod_1",
                use_as="idea_angle",
                fit_reason="失败开场贴合慢任务",
                missing_requirements=["各阶段耗时"],
            )
        ],
        draft_techniques=[
            MethodAssistDraftNote(method_id="emethod_1", note="可用短句开场")
        ],
    )
    runner = FakeRunner(d, ConvergeOutput(recommended_index=1, reason="r"), assist)
    e = IdeaDiverger(runner).explore(_bundle(), methods=[_method()])
    assert MethodAssistOutput in runner.calls
    assert len(e.method_selections) == 1
    assert e.method_selections[0].missing_requirements == ["各阶段耗时"]
    assert e.angles[0].method_id == "emethod_1"
    assert e.draft_techniques[0].note == "可用短句开场"
    # draft technique alone does not create a second idea angle
    assert len(e.angles) == 1


def test_explore_id_changes_when_method_fingerprint_changes():
    d = _diverge(_angle())
    out = ConvergeOutput(recommended_index=1, reason="r")
    m1 = _method(title="A")
    m2 = _method(title="B")
    a = IdeaDiverger(FakeRunner(d, out)).explore(_bundle(), methods=[m1])
    b = IdeaDiverger(FakeRunner(d, out)).explore(_bundle(), methods=[m2])
    c = IdeaDiverger(FakeRunner(d, out)).explore(_bundle(), methods=[m1])
    assert a.id != b.id
    assert a.id == c.id


def test_assist_draft_technique_selection_does_not_add_idea():
    d = _diverge(_angle())
    assist = MethodAssistOutput(
        method_selections=[
            MethodAssistSelection(
                method_id="emethod_1",
                fit_reason="短句节奏",
                use_as="draft_technique",
            )
        ],
    )
    runner = FakeRunner(d, ConvergeOutput(recommended_index=1, reason="r"), assist)
    e = IdeaDiverger(runner).explore(_bundle(), methods=[_method()])
    assert any(dt.method_id == "emethod_1" for dt in e.draft_techniques)
    assert e.recommended_index == 1
