"""ProblemService：add / list / show / close / append_attempt；≤3 open 不变式。"""

from finch.problems.service import ProblemService
from finch.storage.repositories import ProblemRepository
from finch.storage.workspace import Workspace


def _service(tmp_path):
    return ProblemService(ProblemRepository(Workspace(tmp_path)))


def test_add_and_show(tmp_path):
    svc = _service(tmp_path)
    p = svc.add(title="Agent 重试何时值得？")
    assert p.status == "open"
    assert p.id.startswith("problem_")
    assert svc.show(p.id).title == "Agent 重试何时值得？"


def test_add_is_idempotent_by_title(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(title="Agent 重试何时值得？")
    b = svc.add(title="Agent 重试何时值得？")
    assert a.id == b.id
    assert len(svc.list()) == 1


def test_add_rejects_fourth_open_problem(tmp_path):
    svc = _service(tmp_path)
    for i in range(3):
        svc.add(title=f"问题 {i}")
    try:
        svc.add(title="问题 3")
    except ValueError as exc:
        assert "max 3" in str(exc)
        return
    raise AssertionError("expected ValueError on 4th open problem")


def test_close_then_add_allowed(tmp_path):
    svc = _service(tmp_path)
    for i in range(3):
        svc.add(title=f"问题 {i}")
    first = svc.list()[0]
    closed = svc.close(first.id, reason="验证完了")
    assert closed.status == "closed"
    assert closed.closed_reason == "验证完了"
    assert closed.closed_at is not None
    svc.add(title="问题 4")  # now allowed


def test_list_filter_by_status(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(title="问题 A")
    svc.close(a.id)
    svc.add(title="问题 B")
    assert {p.status for p in svc.list(status="open")} == {"open"}
    assert svc.list(status="open")[0].title == "问题 B"


def test_append_attempt_is_idempotent(tmp_path):
    svc = _service(tmp_path)
    p = svc.add(title="问题 A")
    p = svc.append_attempt(p.id, "attempt_1")
    p = svc.append_attempt(p.id, "attempt_1")
    assert p.attempt_ids == ["attempt_1"]


def test_show_missing_raises(tmp_path):
    svc = _service(tmp_path)
    try:
        svc.show("problem_nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError")
