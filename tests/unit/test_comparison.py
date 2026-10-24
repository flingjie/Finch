"""ComparisonService + ComparisonRepository：生成/取舍/材料检查/建任务 的确定性行为。"""

import pytest

from finch.comparison.models import (
    ComparisonOutput,
    ComparisonRecommendation,
    ReadinessCheck,
    TopicDirection,
)
from finch.comparison.repository import ComparisonRepository
from finch.comparison.service import ComparisonService, comparison_id
from finch.ingest.resolver import ResolvedSource
from finch.storage.repositories import ContentJobRepository
from finch.storage.workspace import Workspace


class _Runner:
    def __init__(self, compare=None, readiness=None, recommend=None):
        self.compare = compare
        self.readiness = readiness
        self.recommend = recommend
        self.calls = 0
        self.last_prompt = None

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_prompt = prompt
        if output_model is ComparisonOutput:
            return self.compare
        if output_model is ReadinessCheck:
            return self.readiness
        if output_model is ComparisonRecommendation:
            return self.recommend
        raise AssertionError(f"unexpected output_model {output_model}")


def _direction(**overrides) -> TopicDirection:
    data = dict(
        kind="方法解释",
        question="核心问题",
        reader_value="读者所得",
        available_materials="已有材料",
        material_gaps="材料缺口",
        tradeoff="取舍",
    )
    data.update(overrides)
    return TopicDirection(**data)


def _output(directions=None, note="") -> ComparisonOutput:
    return ComparisonOutput(
        directions=directions or [_direction(), _direction(), _direction()], note=note
    )


def _source(**overrides) -> ResolvedSource:
    data = dict(body="素材正文", content_hash="hash1", sample_size=1,
                source_type="text", source_ref=None)
    data.update(overrides)
    return ResolvedSource(**data)


def _service(runner: _Runner, tmp_path) -> tuple[ComparisonService, Workspace]:
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = ComparisonService(runner, ComparisonRepository(ws), ContentJobRepository(ws))
    return svc, ws


def test_generate_sets_deterministic_fields(tmp_path):
    svc, _ = _service(_Runner(compare=_output()), tmp_path)
    record = svc.generate(_source())
    assert record.id.startswith("cmp_")
    assert record.source_type == "text"
    assert record.content_hash == "hash1"
    assert [d.angle_id for d in record.angles] == ["d1", "d2", "d3"]
    assert record.selection is None
    assert record.readiness == "undecided"


def test_generate_idempotent_same_source(tmp_path):
    runner = _Runner(compare=_output())
    svc, _ = _service(runner, tmp_path)
    r1 = svc.generate(_source())
    r2 = svc.generate(_source())
    assert r1.id == r2.id
    assert runner.calls == 1  # 第二次命中已有记录，不再调 LLM


def test_comparison_id_changes_with_context(tmp_path):
    from finch.comparison.service import ComparisonContext

    a = comparison_id("hash1")
    b = comparison_id("hash1", ComparisonContext(reader="小团队"))
    c = comparison_id("hash2")
    assert a != b
    assert a != c


def test_select_rejects_unknown_angle(tmp_path):
    svc, _ = _service(_Runner(compare=_output()), tmp_path)
    record = svc.generate(_source())
    with pytest.raises(ValueError):
        svc.select(record, selected_angle_ids=["d9"], decision="selected")


def test_select_single_derives_final_question(tmp_path):
    svc, _ = _service(
        _Runner(compare=_output(directions=[_direction(question="核心问题1")])), tmp_path
    )
    record = svc.generate(_source())
    record = svc.select(record, selected_angle_ids=["d1"], decision="selected")
    assert record.selection.final_question == "核心问题1"
    assert record.selection.decision == "selected"


def test_select_merge_requires_final_question(tmp_path):
    svc, _ = _service(_Runner(compare=_output()), tmp_path)
    record = svc.generate(_source())
    with pytest.raises(ValueError):
        svc.select(record, selected_angle_ids=["d1", "d2"], decision="selected")


def test_park_and_discard_do_not_create_job(tmp_path):
    runner = _Runner(compare=_output(), readiness=ReadinessCheck(readiness="ready", reason="ok"))
    svc, ws = _service(runner, tmp_path)
    record = svc.generate(_source())
    record = svc.select(record, selected_angle_ids=[], decision="discarded")
    assert record.selection.decision == "discarded"
    assert record.selection.selected_angle_ids == []
    assert ContentJobRepository(ws).list_jobs() == []


def test_materialize_dedup_and_preserves_fields(tmp_path):
    runner = _Runner(
        compare=_output(
            directions=[
                _direction(
                    question="核心问题1",
                    reader_value="读者所得1",
                    available_materials="已有材料1",
                    material_gaps="缺实验记录",
                    tradeoff="价值大但难验证",
                )
            ]
        ),
        readiness=ReadinessCheck(readiness="ready", reason="材料足够"),
    )
    svc, ws = _service(runner, tmp_path)
    record = svc.generate(_source())
    record = svc.select(record, selected_angle_ids=["d1"], decision="selected")
    record = svc.check_readiness(record)
    assert record.readiness == "ready"
    job1 = svc.materialize(record)
    record = svc.repo.get(record.id)
    job2 = svc.materialize(record)
    assert job1.id == job2.id
    assert ContentJobRepository(ws).list_jobs() == [job1]
    # 交接保留缺口与材料边界（ContentJob 持久化字段）
    assert job1.status.value == "proposed"
    assert "缺实验记录" in job1.open_question
    assert "价值大但难验证" in job1.limitations
    assert job1.observation == "已有材料1"
    assert job1.reader_problem == "读者所得1"
    assert job1.evidence_status == "unverified"
    assert job1.source_kind == "note"


def test_materialize_url_sets_external_evidence(tmp_path):
    runner = _Runner(
        compare=_output(directions=[_direction()]),
        readiness=ReadinessCheck(readiness="ready", reason="ok"),
    )
    svc, _ = _service(runner, tmp_path)
    record = svc.generate(_source(source_type="url", source_ref="https://x.com/a"))
    record = svc.select(record, selected_angle_ids=["d1"], decision="selected")
    record = svc.check_readiness(record)
    job = svc.materialize(record)
    assert job.evidence_status == "externally_reported"
    assert job.origin == "synthesis"
    assert job.source_kind == "post"


def test_old_record_with_missing_new_fields_readable(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    d = ws.dir("comparisons")
    (d / "cmp_old.yaml").write_text(
        "id: cmp_old\nsource_type: text\ncontent_hash: hash_old\n"
        "created_at: '2026-01-01T00:00:00Z'\n",
        encoding="utf-8",
    )
    repo = ComparisonRepository(ws)
    record = repo.get("cmp_old")
    assert record is not None
    assert record.selection is None
    assert record.readiness == "undecided"
    assert record.content_job_id is None
    assert record.angles == []
