"""WeeklyReflectionService：定性复盘（指标输入 + LLM 输出校验）。"""

from finch.content.voice import VoiceProfile
from finch.learn.reflection import (
    WeeklyReflection,
    WeeklyReflectionService,
    render_reflection,
)
from finch.learn.weekly import WeeklyReport


class FakeRunner:
    def __init__(self, ret):
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        return self.ret


def _report() -> WeeklyReport:
    return WeeklyReport(reviewed_drafts=2, approved=1, approval_rate=0.5)


def test_reflect_returns_reflection():
    svc = WeeklyReflectionService(FakeRunner(WeeklyReflection(
        insight="想清楚了 X", strongest_expression="那篇回复", meaningful_connection="一次追问",
        next_practice="先讲场景再下判断", stop_doing="堆术语",
        voice_update_candidate="少用'本质上'", new_idea_candidates=["idea_x"],
    )))
    out = svc.reflect(_report(), voice_profile=VoiceProfile())
    assert out.insight == "想清楚了 X"
    assert out.new_idea_candidates == ["idea_x"]


def test_render_reflection_leads_with_practice():
    reflection = WeeklyReflection(
        insight="i",
        strongest_expression="那篇回复",
        meaningful_connection="与 Alice 的来回",
        surface_only_interactions="点赞帖",
        conversations_formed_ideas="关系记忆讨论",
        continue_relationships="Alice；Bob",
        next_practice="先讲场景再下判断",
        stop_doing="堆术语",
        voice_update_candidate="少用本质上",
    )
    text = render_reflection(reflection)
    assert text.startswith("下周只练：先讲场景再下判断")
    assert "本周真正有来回：与 Alice 的来回" in text
    assert "下周继续：Alice；Bob" in text
    assert "其余：" in text
    assert "Finch Weekly Reflection" not in text
    assert "uv run finch practice start" in text or "uv run finch connect daily" in text
    assert text.index("下周只练：") < text.index("其余：")
