"""WritingStyleService：analyze 覆盖确定性字段 + compare 三桶。"""

from finch.content.voice import VoiceProfile
from finch.style.models import StyleComparison, StyleReport
from finch.style.service import WritingStyleService
from finch.style.source_resolver import ResolvedSource


class _Runner:
    def __init__(self, ret):
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        return self.ret


def _source():
    return ResolvedSource(body="正文", content_hash="abc", sample_size=3,
                          source_type="file", source_ref="p.md")


def test_analyze_overrides_deterministic_fields():
    raw = StyleReport(
        id="model-set", source_type="url", content_hash="model-hash", sample_size=99,
        opening=[], overall_confidence="high",
    )
    svc = WritingStyleService(_Runner(raw))
    report = svc.analyze(_source())
    # 确定性字段被代码覆盖，不信模型。
    assert report.id != "model-set"
    assert report.source_type == "file"
    assert report.content_hash == "abc"
    assert report.sample_size == 3
    assert report.overall_confidence == "high"  # 判断字段保留


def test_compare_returns_buckets():
    svc = WritingStyleService(_Runner(StyleComparison(
        already_shared=["a"], worth_experimenting=["b"], not_a_fit=["c"],
    )))
    out = svc.compare(StyleReport(), VoiceProfile())
    assert out.worth_experimenting == ["b"]
