"""PracticeAttemptService：add / verify / close / problem 回链。"""

from finch.practice.attempts_service import PracticeAttemptService
from finch.storage.repositories import PracticeAttemptRepository, ProblemRepository
from finch.storage.workspace import Workspace


def _service(tmp_path):
    ws = Workspace(tmp_path)
    return PracticeAttemptService(
        PracticeAttemptRepository(ws), ProblemRepository(ws)
    )


def test_add_attempt(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(problem="重试何时值得", attempt="加重试", observation="多数失败卡在输入",
                unknown="阈值怎么定", next_step="测 3 个案例")
    assert a.status == "open"
    assert a.id.startswith("attempt_")
    assert a.unknown == "阈值怎么定"


def test_add_is_idempotent(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(problem="P", attempt="T", observation="O")
    b = svc.add(problem="P", attempt="T", observation="O")
    assert a.id == b.id
    assert len(svc.list()) == 1


def test_verify_sets_result(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(problem="P", attempt="T", observation="O")
    v = svc.verify(a.id, result="3 个案例里 2 个重试有效")
    assert v.status == "verified"
    assert v.result == "3 个案例里 2 个重试有效"


def test_verify_requires_open(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(problem="P", attempt="T", observation="O")
    svc.verify(a.id, result="r")
    try:
        svc.verify(a.id, result="again")
    except ValueError as exc:
        assert "verified" in str(exc)
        return
    raise AssertionError("expected ValueError on non-open verify")


def test_add_with_problem_id_links_attempt(tmp_path):
    ws = Workspace(tmp_path)
    from finch.problems.service import ProblemService

    problems = ProblemRepository(ws)
    p = ProblemService(problems).add(title="重试何时值得")
    svc = PracticeAttemptService(PracticeAttemptRepository(ws), problems)
    a = svc.add(problem_id=p.id, problem="重试何时值得", attempt="T", observation="O")
    linked = problems.get(p.id)
    assert linked.attempt_ids == [a.id]


def test_add_with_missing_problem_raises(tmp_path):
    svc = _service(tmp_path)
    try:
        svc.add(problem_id="problem_nope", problem="P", attempt="T", observation="O")
    except ValueError as exc:
        assert "problem not found" in str(exc)
        return
    raise AssertionError("expected ValueError on missing problem")
