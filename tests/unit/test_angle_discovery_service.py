"""AngleDiscoveryService：discover 覆盖确定性字段 + prompt 注入。"""

from finch.angle_discovery.models import AngleBrief, AngleCard, SourceSummary
from finch.angle_discovery.service import AngleContext, AngleDiscoveryService, brief_id
from finch.ingest.resolver import ResolvedSource


class _Runner:
    def __init__(self, ret):
        self.ret = ret
        self.last_prompt = None
        self.calls = 0

    def run(self, prompt, output_model, **kw):
        self.last_prompt = prompt
        self.calls += 1
        return self.ret


def _source() -> ResolvedSource:
    return ResolvedSource(
        body="AI 让写代码更快，但交付没有同步变快。",
        content_hash="abc123",
        sample_size=1,
        source_type="file",
        source_ref="a.md",
    )


def _raw_brief(**overrides) -> AngleBrief:
    data = dict(
        id="model-set",
        source_type="url",
        content_hash="model-hash",
        source_summary=SourceSummary(main_point="AI 让个人编码更快。"),
        angles=[AngleCard(title="t", thesis="中心", incremental_value="增量")],
    )
    data.update(overrides)
    return AngleBrief(**data)


def test_discover_overrides_deterministic_fields():
    runner = _Runner(_raw_brief())
    brief = AngleDiscoveryService(runner).discover(_source())
    assert brief.id != "model-set"
    assert brief.id.startswith("angle_")
    assert brief.source_type == "file"
    assert brief.source_ref == "a.md"
    assert brief.content_hash == "abc123"
    assert "AI 让写代码更快" in (runner.last_prompt or "")


def test_discover_id_stable_for_same_hash():
    svc = AngleDiscoveryService(_Runner(_raw_brief()))
    assert svc.discover(_source()).id == svc.discover(_source()).id


def test_discover_prompt_includes_angle_library():
    runner = _Runner(_raw_brief())
    AngleDiscoveryService(runner).discover(_source())
    prompt = runner.last_prompt or ""
    assert "系统瓶颈" in prompt
    assert "increment_basis" in prompt


def test_discover_prompt_includes_context():
    runner = _Runner(_raw_brief())
    ctx = AngleContext(reader="小团队", reader_problem="交付慢", preferred_angles=["系统瓶颈"])
    AngleDiscoveryService(runner).discover(_source(), ctx)
    prompt = runner.last_prompt or ""
    assert "小团队" in prompt
    assert "系统瓶颈" in prompt


def test_discover_prompt_includes_sample_size():
    runner = _Runner(_raw_brief())
    src = _source().model_copy(update={"sample_size": 3})
    AngleDiscoveryService(runner).discover(src)
    assert "Sample count\n3" in (runner.last_prompt or "")


def test_discover_merges_deterministic_coverage():
    gap = "该 X 线程包含媒体（图片/视频），本次未读取其内容"
    src = _source().model_copy(update={"coverage": [gap]})
    runner = _Runner(_raw_brief())
    brief = AngleDiscoveryService(runner).discover(src)
    assert brief.coverage == [gap]


def test_brief_id_public_helper():
    assert brief_id("abc123") == brief_id("abc123")
    assert brief_id("abc123") != brief_id("other")
