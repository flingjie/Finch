"""ContentSummaryService：summarize 覆盖确定性字段。"""

from finch.content_summary.models import ContentSummary, EvidencePoint
from finch.content_summary.service import ContentSummaryService
from finch.ingest.resolver import ResolvedSource


class _Runner:
    def __init__(self, ret):
        self.ret = ret
        self.last_prompt = None

    def run(self, prompt, output_model, **kw):
        self.last_prompt = prompt
        return self.ret


def _source() -> ResolvedSource:
    return ResolvedSource(
        body="测试通过，但问题没解决。记忆不是聊天记录。",
        content_hash="abc123",
        sample_size=1,
        source_type="file",
        source_ref="a.md",
    )


def _raw_summary(**overrides) -> ContentSummary:
    data = dict(
        id="model-set",
        source_type="url",
        content_hash="model-hash",
        main_point="测试通过不等于问题解决",
        key_points=["测试通过只是必要条件", "记忆不是聊天记录"],
        evidence=[EvidencePoint(source="作者", content="测试通过，但问题没解决")],
        conditions=["原文未说明适用范围"],
    )
    data.update(overrides)
    return ContentSummary(**data)


def test_summarize_overrides_deterministic_fields():
    runner = _Runner(_raw_summary())
    summary = ContentSummaryService(runner).summarize(_source())
    assert summary.id != "model-set"
    assert summary.id.startswith("summary_")
    assert summary.source_type == "file"
    assert summary.source_ref == "a.md"
    assert summary.content_hash == "abc123"
    assert summary.main_point == "测试通过不等于问题解决"
    assert summary.evidence[0].source == "作者"
    assert "测试通过" in (runner.last_prompt or "")


def test_summarize_id_stable_for_same_hash():
    svc = ContentSummaryService(_Runner(_raw_summary()))
    a = svc.summarize(_source())
    b = svc.summarize(_source())
    assert a.id == b.id


def test_summarize_prompt_includes_sample_size():
    runner = _Runner(_raw_summary())
    src = _source().model_copy(update={"sample_size": 3})
    ContentSummaryService(runner).summarize(src)
    prompt = runner.last_prompt or ""
    assert "Sample count\n3" in prompt
