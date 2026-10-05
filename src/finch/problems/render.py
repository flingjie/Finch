"""把活跃问题渲染成 prompt 文本块（镜像 ``profile/render.py::render_user_practices``）。"""

from finch.problems.models import ActiveProblem
from finch.profile.render import NONE_MARKER


def render_active_problems(problems: list[ActiveProblem] | None) -> str:
    """只渲染 ``status == "open"`` 的活跃问题；空/None → ``(none)``。"""
    if not problems:
        return NONE_MARKER
    open_problems = [p for p in problems if p.status == "open"]
    if not open_problems:
        return NONE_MARKER
    lines: list[str] = []
    for p in open_problems:
        head = f"- [{p.id}] {p.title}"
        if p.why_it_matters:
            head += f" — {p.why_it_matters}"
        lines.append(head)
    return "\n".join(lines)
