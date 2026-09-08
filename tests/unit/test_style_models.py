"""writing-style-analysis 数据模型。"""

from finch.style.models import StyleComparison, StyleEvidence, StyleReport


def test_style_report_defaults_are_empty():
    r = StyleReport()
    assert r.id == ""
    assert r.source_type == "text"
    assert r.sample_size == 1
    assert r.scope == "single_text"
    assert r.opening == []
    assert r.signature_patterns == []


def test_style_evidence_holds_excerpts():
    e = StyleEvidence(dimension="opening", observation="直接给结论",
                      excerpts=["第一句就下判断"], confidence="high")
    assert e.dimension == "opening"
    assert e.excerpts == ["第一句就下判断"]


def test_style_comparison_three_buckets():
    c = StyleComparison(
        already_shared=["先写具体问题"],
        worth_experimenting=["用失败场景替代背景"],
        not_a_fit=["强断言"],
    )
    assert c.worth_experimenting == ["用失败场景替代背景"]
