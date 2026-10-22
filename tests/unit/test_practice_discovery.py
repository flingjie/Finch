"""PracticeDiscoveryService：确定性已有记录 → ≤3 张卡；problem_relevance 独立。"""

from datetime import UTC, datetime

from finch.expression_methods.models import ExpressionMethod
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.practice.discovery import PracticeDiscoveryService, problem_relevance
from finch.problems.models import ActiveProblem
from finch.problems.service import ProblemService
from finch.storage.repositories import (
    KnowledgeCardRepository,
    PracticeAttemptRepository,
    ProblemRepository,
)
from finch.storage.workspace import Workspace


def _method(title, why_effective, when_to_use="", mini_exercise=""):
    now = datetime.now(UTC)
    return ExpressionMethod(
        id=f"emethod_{title}",
        title=title,
        why_effective=why_effective,
        when_to_use=when_to_use,
        mini_exercise=mini_exercise,
        created_at=now,
        updated_at=now,
    )


def _service(tmp_path):
    ws = Workspace(tmp_path)
    svc = PracticeDiscoveryService(
        problems=ProblemRepository(ws),
        methods=ExpressionMethodRepository(ws),
        knowledge=KnowledgeCardRepository(ws),
        attempts=PracticeAttemptRepository(ws),
    )
    return svc, ws


def test_discover_returns_existing_method_cards(tmp_path):
    svc, ws = _service(tmp_path)
    p = ProblemService(ProblemRepository(ws)).add(
        title="项目汇报讲不清楚", desired_outcome="一次讲清重点"
    )
    ExpressionMethodRepository(ws).upsert(
        _method(
            "金字塔原理",
            "先结论后论据，能让汇报一次讲清",
            when_to_use="汇报/写作",
            mini_exercise="把一段汇报改写成结论先行",
        )
    )
    cards = svc.discover(p.id)
    assert cards
    assert all(c.origin == "existing" for c in cards)
    assert all(c.problem_id == p.id for c in cards)
    assert len(cards) <= 3


def test_discover_caps_at_three(tmp_path):
    svc, ws = _service(tmp_path)
    p = ProblemService(ProblemRepository(ws)).add(title="汇报")
    for i in range(10):
        ExpressionMethodRepository(ws).upsert(
            _method(f"方法{i}", "汇报相关", mini_exercise="做一次")
        )
    cards = svc.discover(p.id)
    assert len(cards) == 3


def test_discover_missing_problem_raises(tmp_path):
    svc, _ = _service(tmp_path)
    try:
        svc.discover("problem_nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_problem_relevance_overlap():
    p = ActiveProblem(
        id="problem_x",
        title="项目汇报说不清楚",
        desired_outcome="一次讲清重点",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert problem_relevance(p, "金字塔原理让汇报一次讲清重点") > 0
    assert problem_relevance(p, "完全不相关的烹饪技巧") == 0.0
