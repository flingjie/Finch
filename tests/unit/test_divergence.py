"""Tests for IdeaDiverger（发散 → 确定性核验 → 收敛）。"""

from finch.ideas.divergence import ConvergeOutput, DivergeOutput, IdeaDiverger
from finch.ideas.models import FactBundle, IdeaBoundaries, SourceRef


class FakeRunner:
    def __init__(self, diverge_out: DivergeOutput, converge_out: ConvergeOutput):
        self.diverge_out = diverge_out
        self.converge_out = converge_out
        self.calls: list[type] = []

    def run(self, prompt, output_model, **kw):
        self.calls.append(output_model)
        if output_model is DivergeOutput:
            return self.diverge_out
        if output_model is ConvergeOutput:
            return self.converge_out
        raise AssertionError(f"unexpected model {output_model}")


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


def test_explore_same_input_same_id():
    d = _diverge(_angle())
    out = ConvergeOutput(recommended_index=1, reason="r")
    a = IdeaDiverger(FakeRunner(d, out)).explore(_bundle())
    b = IdeaDiverger(FakeRunner(d, out)).explore(_bundle())
    assert a.id == b.id
