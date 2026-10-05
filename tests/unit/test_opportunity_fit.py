"""Fit.problem_refs：活跃问题回链（默认空、旧数据可加载）。"""

from finch.opportunities.models import Fit


def test_fit_problem_refs_defaults_empty():
    fit = Fit(reason="r")
    assert fit.problem_refs == []
    assert fit.practice_refs == []


def test_fit_problem_refs_roundtrip():
    fit = Fit(reason="r", practice_refs=["agent-100-days"], problem_refs=["problem_abc"])
    assert fit.problem_refs == ["problem_abc"]
