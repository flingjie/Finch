"""《精简写作》48 条种子方法：导入、幂等、可执行标志、同义合并。"""

from datetime import UTC, datetime

from finch.article.repository import ArticleReportRepository
from finch.expression_methods.models import ExpressionMethod
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.expression_methods.seed_cw48 import CW48_SEED
from finch.expression_methods.service import ExpressionMethodService
from finch.storage.workspace import Workspace

_NON_EXECUTABLE = {
    "EP-CW-005",
    "EP-CW-006",
    "EP-CW-012",
    "EP-CW-024",
    "EP-CW-025",
    "EP-CW-026",
    "EP-CW-035",
    "EP-CW-042",
    "EP-CW-043",
}


class NoopRunner:
    def run(self, prompt, output_model, **kw):
        raise AssertionError("seed must not call the LLM")


def _service(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    return ws, ExpressionMethodService(
        ExpressionMethodRepository(ws), ArticleReportRepository(ws), NoopRunner()
    )


def test_seed_data_has_48_unique_ids():
    assert len(CW48_SEED) == 48
    ids = [row["id"] for row in CW48_SEED]
    assert len(set(ids)) == 48
    assert ids[0] == "EP-CW-001" and ids[-1] == "EP-CW-048"


def test_seed_imports_48_and_is_idempotent(tmp_path):
    ws, svc = _service(tmp_path)
    created, merged, skipped = svc.seed_cw48()
    assert len(created) == 48
    assert merged == []
    assert skipped == []

    created2, merged2, skipped2 = svc.seed_cw48()
    assert created2 == []
    assert merged2 == []
    assert len(skipped2) == 48
    assert len(ExpressionMethodRepository(ws).list_all()) == 48


def test_seed_force_overwrites(tmp_path):
    ws, svc = _service(tmp_path)
    svc.seed_cw48()
    created, merged, skipped = svc.seed_cw48(force=True)
    assert len(created) == 48
    assert merged == []
    assert skipped == []
    assert len(ExpressionMethodRepository(ws).list_all()) == 48


def test_seed_flags_and_provenance(tmp_path):
    ws, svc = _service(tmp_path)
    svc.seed_cw48()
    methods = ExpressionMethodRepository(ws).list_all()
    assert len(methods) == 48
    non_exec = {m.id for m in methods if not m.executable}
    assert non_exec == _NON_EXECUTABLE
    for m in methods:
        assert m.evidence_status == "user_supplied_toc_summary"
        assert "目录层面整理" in m.source_note
        assert m.method_type in ("technique", "training", "scenario")
    # 首批优先启用条目应可执行
    for mid in ("EP-CW-010", "EP-CW-013", "EP-CW-015", "EP-CW-019", "EP-CW-022",
                "EP-CW-027", "EP-CW-029", "EP-CW-031", "EP-CW-046"):
        assert any(m.id == mid and m.executable for m in methods)


def test_seed_merges_by_exact_title(tmp_path):
    ws, svc = _service(tmp_path)
    now = datetime.now(UTC)
    ExpressionMethodRepository(ws).upsert(
        ExpressionMethod(
            id="emethod_x",
            title="围绕要点写",  # 与 EP-CW-001 同名
            why_effective="",
            when_to_use="",
            mini_exercise="",
            created_at=now,
            updated_at=now,
        )
    )
    created, merged, skipped = svc.seed_cw48()
    assert "EP-CW-001" not in created
    assert "emethod_x" in merged
    assert ExpressionMethodRepository(ws).get("EP-CW-001") is None
    attached = ExpressionMethodRepository(ws).get("emethod_x")
    assert "《精简写作》目录（对应 EP-CW-001）" in attached.source_note

    # 幂等：第二次不再重复 merge
    created2, merged2, skipped2 = svc.seed_cw48()
    assert merged2 == []
    assert "EP-CW-001" in skipped2
