"""render_active_problems：只渲染 open 问题，空/None → (none)。"""

from datetime import UTC, datetime

from finch.problems.models import ActiveProblem
from finch.problems.render import render_active_problems


def _p(title, status="open"):
    return ActiveProblem(
        id=f"problem_{title[:4]}", title=title, status=status,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )


def test_renders_open_only():
    out = render_active_problems([_p("重试何时值得"), _p("已关闭", status="closed")])
    assert "重试何时值得" in out
    assert "已关闭" not in out


def test_empty_returns_none_marker():
    assert render_active_problems([]) == "(none)"
    assert render_active_problems(None) == "(none)"
    assert render_active_problems([_p("x", status="closed")]) == "(none)"
